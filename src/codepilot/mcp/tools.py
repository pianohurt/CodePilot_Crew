"""发现外部工具，并以远端 Schema 校验本地调用。"""

from contextlib import AsyncExitStack
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from codepilot.agent.types import ToolDefinition
from codepilot.core.context import ExecutionContext
from codepilot.mcp.client import McpError, StdioClient
from codepilot.mcp.config import McpConfig
from codepilot.tools.registry import PreparedAction, ToolError, ToolRegistry


async def connect_tools(
    config: McpConfig, workspace: Path, registry: ToolRegistry, resources: AsyncExitStack,
) -> None:
    for name, server in config.servers.items():
        client = StdioClient(name, server)
        resources.push_async_callback(client.close)
        await client.start(workspace)
        await discover(client, registry)


async def discover(client: StdioClient, registry: ToolRegistry) -> None:
    discovered: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(16):
        page = await client.request("tools/list", {"cursor": cursor} if cursor is not None else {})
        tools = page.get("tools")
        if not isinstance(tools, list):
            raise McpError(f"MCP {client.name}: invalid tool list")
        for tool in tools:
            name = tool.get("name") if isinstance(tool, dict) else None
            if not isinstance(name, str) or not name or len(name) > 128:
                raise McpError(f"MCP {client.name}: invalid tool name")
            if name in discovered:
                raise McpError(f"MCP {client.name}: duplicate remote tool {name}")
            discovered[name] = tool
            if len(discovered) > 128:
                raise McpError(f"MCP {client.name}: tool discovery limit exceeded")
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise McpError(f"MCP {client.name}: invalid tool cursor")
        seen.add(cursor)
    else:
        raise McpError(f"MCP {client.name}: tool pagination limit exceeded")

    selected = discovered.keys() if client.config.tools is None else client.config.tools
    for name in selected:
        if name not in discovered:
            raise McpError(f"MCP {client.name}: configured tool is missing: {name}")
        register_tool(client, registry, name, discovered[name])


def register_tool(client: StdioClient, registry: ToolRegistry, remote: str, tool: dict[str, Any]) -> None:
    name = f"mcp__{client.name}__" + re.sub(r"[^a-zA-Z0-9_-]", "_", remote)
    schema = tool.get("inputSchema")
    description = tool.get("description", "")
    if len(name) > 64 or not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(description, str):
        raise McpError(f"MCP {client.name}: invalid tool definition for {remote}")
    if "$schema" in schema and not isinstance(schema["$schema"], str):
        raise McpError(f"MCP {client.name}: invalid Schema dialect for {remote}")
    dialect = validator_for(schema, default=None) if "$schema" in schema else Draft202012Validator
    if dialect is None:
        raise McpError(f"MCP {client.name}: unsupported Schema dialect for {remote}")
    try:
        dialect.check_schema(schema)
    except (SchemaError, RecursionError) as exc:
        raise McpError(f"MCP {client.name}: invalid input Schema for {remote}") from exc
    # 显式空注册表允许文档内引用，同时阻止校验器访问外部地址。
    validator = dialect(schema, registry=Registry())

    def check(arguments: dict[str, Any]) -> None:
        try:
            validator.validate(arguments)
        except ValidationError as exc:
            location = ".".join(map(str, exc.absolute_path)) or "input"
            raise ToolError(f"invalid_arguments: {location} ({exc.validator})") from exc
        except (Unresolvable, RecursionError) as exc:
            raise ToolError("mcp_schema_reference_unavailable") from exc

    def prepare(arguments: dict[str, Any], context: ExecutionContext) -> PreparedAction:
        async def invoke() -> str:
            try:
                result = await client.request("tools/call", {"name": remote, "arguments": arguments})
            except McpError as exc:
                raise ToolError(str(exc)) from exc
            except OSError as exc:
                raise ToolError(f"MCP {client.name}: connection lost") from exc
            return observation(result)

        preview = {"server": client.name, "tool": remote, "arguments": arguments}
        # 串行排队受根任务约束，单次传输由客户端单独计时。
        return PreparedAction(preview, invoke, None)

    try:
        registry.register_external(ToolDefinition(name=name, description=description or remote, input_schema=schema), check, prepare)
    except ValueError as exc:
        raise McpError(str(exc)) from exc


def observation(result: dict[str, Any]) -> str:
    is_error = result.get("isError", False)
    content = result.get("content")
    structured = result.get("structuredContent")
    if type(is_error) is not bool or not isinstance(content, list) or (
        "structuredContent" in result and not isinstance(structured, dict)
    ):
        raise ToolError("mcp_invalid_tool_result")
    parts: list[str] = []
    unsupported = False
    for block in content:
        if not isinstance(block, dict):
            raise ToolError("mcp_invalid_tool_result")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            unsupported = True
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False))
    text = "\n".join(parts)
    if is_error:
        raise ToolError("mcp_tool_error" + (f": {text}" if text else ""))
    if unsupported:
        raise ToolError("mcp_unsupported_result_content")
    return text or "(empty MCP result)"
