"""用官方 filesystem stdio 服务读取临时文件，模型可固定或真实。"""

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolUse
from codepilot.core.config import load_model_settings
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.mcp.config import McpConfig, ServerConfig
from codepilot.mcp.tools import connect_tools
from codepilot.tools.registry import ToolRegistry

TOOL = "mcp__fs__read_text_file"


async def verify(entry: Path, env_file: Path | None) -> bool:
    with TemporaryDirectory(prefix="codepilot-s7-") as directory:
        workspace = Path(directory).resolve()
        marker = "MCP_PROOF_" + uuid4().hex[:12]
        file = workspace / "proof.txt"
        file.write_text(marker, encoding="utf-8")
        config = McpConfig(servers={
            "fs": ServerConfig(command="node", args=[str(entry.resolve(strict=True)), str(workspace)], tools=["read_text_file"]),
        })
        async with AsyncExitStack() as resources:
            registry = ToolRegistry()
            await connect_tools(config, workspace, registry, resources)
            secrets = ()
            model_name = "fixed"
            if env_file is not None:
                settings = load_model_settings(env_file)
                model_name = settings.model
                secrets = (settings.api_key.get_secret_value(),)
                client = await resources.enter_async_context(httpx.AsyncClient(timeout=settings.model_timeout))
                provider = AnthropicProvider(
                    client, base_url=settings.base_url, api_key=secrets[0], model=settings.model,
                    max_tokens=settings.max_tokens, context_budget=settings.context_budget,
                )
            else:
                provider = FixedResponseProvider([
                    ModelResponse(stop_reason="tool_use", content=[ToolUse(id="read", name=TOOL, input={"path": str(file)})]),
                    ModelResponse(stop_reason="end_turn", content=[TextBlock(text=marker)]),
                ])
            events = []
            result = await Runner(
                provider, registry, workspace, policy=PermissionPolicy(mcp_tools={TOOL: "allow"}), secrets=secrets,
            ).run(
                f"Use {TOOL} to read {file.as_posix()} and report the exact text found.", on_event=events.append,
            )
        actual_read = any(
            event.type == "tool.completed" and event.data["name"] == TOOL
            and not event.data["is_error"] and marker in event.data["content"] for event in events
        )
        passed = result.status == "success" and actual_read and marker in result.answer
        print(json.dumps({
            "model": model_name, "passed": passed, "tool": TOOL, "actual_read": actual_read,
            "answer_contains_marker": marker in result.answer, "stop_reason": result.stop_reason,
            "steps": result.steps,
        }))
        return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--filesystem-entry", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(verify(args.filesystem_entry, args.env_file)) else 1)
