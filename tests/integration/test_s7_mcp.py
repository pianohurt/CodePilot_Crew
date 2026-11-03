import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys
from typing import Any

import pytest

import codepilot.core.app as core_app
from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolResult, ToolUse
from codepilot.core.config import ModelSettings, Settings
from codepilot.core.context import ExecutionContext
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.mcp.client import McpError, StdioClient
from codepilot.mcp.config import McpConfig, ServerConfig, load_mcp_config
from codepilot.mcp.tools import connect_tools, discover, register_tool
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RpcClient
from codepilot.transport.server import SocketServer
from test_s3_commands import is_running

DEMO = Path(__file__).resolve().parents[2] / "scripts" / "mcp_demo_server.py"


def config(label: str, *options: str, **kwargs: Any) -> ServerConfig:
    return ServerConfig(command=sys.executable, args=[str(DEMO), "--label", label, *options], **kwargs)


async def test_runner_discovers_namespaces_validates_schema_and_requests_approval(tmp_path: Path) -> None:
    registry = builtin_registry()
    tool_a = "mcp__left__echo"
    tool_b = "mcp__right__echo"
    provider = FixedResponseProvider([
        ModelResponse(stop_reason="tool_use", content=[
            ToolUse(id="bad", name=tool_a, input={"text": 7}),
            ToolUse(id="ask", name=tool_a, input={"text": "中文"}),
            ToolUse(id="allowed", name=tool_b, input={"text": "proof"}),
        ]),
        ModelResponse(stop_reason="end_turn", content=[TextBlock(text="done")]),
    ])
    previews = []
    events = []

    async def approve(call: ToolUse, preview: dict[str, Any], context: ExecutionContext) -> bool:
        previews.append(preview)
        return True

    async with AsyncExitStack() as resources:
        await connect_tools(McpConfig(servers={"left": config("left", "--faults", tools=["echo"]), "right": config("right")}),
                            tmp_path, registry, resources)
        definitions = {definition.name: definition for definition in registry.definitions(registry.names)}
        assert definitions[tool_a].input_schema["properties"]["text"] == {"$ref": "#/$defs/text"}
        assert "mcp__left__write_once" not in definitions
        runner = Runner(provider, registry, tmp_path, policy=PermissionPolicy(mcp_tools={tool_b: "allow"}))
        result = await runner.run("Check both tools", approver=approve, on_event=events.append)
        assert result.status == "success"
        assert [(item.tool_use_id, item.is_error) for item in result.tool_calls] == [("bad", True), ("ask", False), ("allowed", False)]
        assert [block.content for block in provider.requests[-1][-1].content if isinstance(block, ToolResult)] == [
            "invalid_arguments: text (type)", "left: 中文", "right: proof",
        ]
        assert [item.tool_use_id for item in events if item.type == "tool.started"] == ["ask", "allowed"]
        assert previews == [{"server": "left", "tool": "echo", "arguments": {"text": "中文"}}]


@pytest.mark.parametrize("effect", ["error", "rpc", "timeout", "disconnect", "cancel"])
async def test_failed_write_is_never_replayed_and_broken_connections_are_quarantined(tmp_path: Path, effect: str) -> None:
    registry = builtin_registry()
    client = StdioClient("demo", config("demo", "--faults", timeout_seconds=1.5))
    tool = "mcp__demo__write_once"
    context = ExecutionContext(tmp_path, frozenset({tool}), policy=PermissionPolicy(mcp_tools={tool: "allow"}))
    path = tmp_path / "demo.writes"
    try:
        await client.start(tmp_path)
        await discover(client, registry)
        pid = int((tmp_path / "demo.pid").read_text())
        call = ToolUse(id="one", name=tool, input={"effect": "timeout" if effect == "cancel" else effect})
        if effect == "cancel":
            task = asyncio.create_task(registry.execute(call, context))
            async with asyncio.timeout(4):
                while not path.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await registry.execute(call, context)
            assert result.is_error
            expected = {"error": "written, then failed", "rpc": "simulated RPC error", "timeout": "timed out",
                        "disconnect": "connection closed"}
            assert expected[effect] in result.content
        if effect in {"error", "rpc"}:
            reply = await client.request("tools/call", {"name": "echo", "arguments": {"text": "still alive"}})
            assert reply["content"][0]["text"] == "demo: still alive"
        else:
            unavailable = await registry.execute(ToolUse(id="two", name=tool, input={"effect": "error"}), context)
            assert unavailable.is_error and "connection unavailable" in unavailable.content
            assert client.process is not None and client.process.returncode is not None
        assert path.read_text(encoding="utf-8").splitlines() == ["written"]
    finally:
        await client.close()
    assert not is_running(pid)


async def test_normalized_duplicate_name_fails_and_startup_unwinds(tmp_path: Path) -> None:
    with pytest.raises(McpError, match="Duplicate tool: mcp__demo__same_name"):
        async with AsyncExitStack() as resources:
            settings = McpConfig(servers={"demo": config("demo", "--faults", "--collision")})
            await connect_tools(settings, tmp_path, builtin_registry(), resources)
    assert not is_running(int((tmp_path / "demo.pid").read_text()))


async def test_unresolved_schema_reference_and_unapproved_call_do_not_reach_server(tmp_path: Path) -> None:
    registry = builtin_registry()
    client = StdioClient("demo", config("demo"))
    try:
        await client.start(tmp_path)
        await discover(client, registry)
        register_tool(client, registry, "invalid_ref", {"inputSchema": {
            "type": "object", "properties": {"value": {"$ref": "https://example.invalid/schema"}},
        }})
        context = ExecutionContext(tmp_path, registry.names)
        denied = await registry.execute(ToolUse(id="ask", name="mcp__demo__echo", input={"text": "hello"}), context)
        invalid = await registry.execute(ToolUse(id="ref", name="mcp__demo__invalid_ref", input={"value": 1}), context)
        assert denied.is_error and denied.content.startswith("approval_unavailable")
        assert invalid.is_error and invalid.content == "mcp_schema_reference_unavailable"
    finally:
        await client.close()


async def test_core_shutdown_reaps_server_with_an_active_mcp_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = "mcp__demo__write_once"
    provider = FixedResponseProvider([
        ModelResponse(stop_reason="tool_use", content=[ToolUse(id="write", name=tool, input={"effect": "timeout"})]),
    ])
    monkeypatch.setattr(core_app, "AnthropicProvider", lambda *args, **kwargs: provider)
    ready = asyncio.get_running_loop().create_future()

    class TestServer(SocketServer):
        def __init__(self, handler: Any, **options: Any) -> None:
            super().__init__(handler, port=0)

        async def start(self) -> None:
            await super().start()
            ready.set_result(self.port)

    monkeypatch.setattr(core_app, "SocketServer", TestServer)
    path = tmp_path / "mcp.json"
    path.write_text(McpConfig(servers={"demo": config("demo", "--faults")}).model_dump_json(), encoding="utf-8")
    task = asyncio.create_task(core_app.run_core(
        Settings(), workspace=tmp_path, model=ModelSettings(api_key="fake", base_url="https://test.invalid", model="fake"),
        mcp_config=load_mcp_config(path), policy=PermissionPolicy(mcp_tools={tool: "allow"}),
    ))
    try:
        port = await asyncio.wait_for(ready, 6)
        client = RpcClient("127.0.0.1", port)
        await client.connect()
        try:
            await client.call("agent.start", {"goal": "exercise shutdown"})
            async with asyncio.timeout(4):
                while not (tmp_path / "demo.writes").exists():
                    await asyncio.sleep(0.01)
            pid = int((tmp_path / "demo.pid").read_text())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert not is_running(pid)
            assert (tmp_path / "demo.writes").read_text(encoding="utf-8").splitlines() == ["written"]
        finally:
            await client.close()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
