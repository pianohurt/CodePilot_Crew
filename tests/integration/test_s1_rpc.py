import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.client import ProtocolError, RemoteError, request
from codepilot.transport.server import SocketServer


async def run_cli(port: int, goal: str, steps: int = 8) -> tuple[int, dict[str, object]]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CODEPILOT_CREW_")}
    env["PYTHONUTF8"] = "1"
    # 即使调用环境强制 ASCII，命令行的结构化输出也应使用 UTF-8。
    env["PYTHONIOENCODING"] = "ascii"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "codepilot.cli", "run", goal, "--port", str(port), "--max-steps", str(steps), "--json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert not stderr, stderr.decode("utf-8")
        return process.returncode, json.loads(stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_cli_rpc_fixed_response_real_file_round_trip(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("actual integration input", encoding="utf-8")
    provider = FixedResponseProvider([
        ModelResponse(content=[ToolUse(id="file-call", name="read_file", input={"path": "sample.txt"})], stop_reason="tool_use"),
        ModelResponse(content=[TextBlock(text="固定回答：验证通过 ✅")], stop_reason="end_turn"),
    ])
    server = SocketServer(CoreService(Runner(provider, readonly_registry(), tmp_path)).handle, port=0)
    await server.start()
    try:
        code, result = await run_cli(server.port, "read sample.txt")
        assert code == 0 and result["status"] == "success" and result["answer"] == "固定回答：验证通过 ✅"
        assert result["tool_calls"][0]["tool_use_id"] == "file-call"
        assert "actual integration input" in provider.requests[1][-1].content[0].content
        assert (await request(server.host, server.port, "core.ping", {}))["message"] == "pong"
    finally:
        await server.close()


async def test_failed_run_is_not_confused_with_successful_rpc(tmp_path: Path) -> None:
    provider = FixedResponseProvider([
        ModelResponse(content=[ToolUse(id="list", name="list_dir", input={})], stop_reason="tool_use")
    ])
    server = SocketServer(CoreService(Runner(provider, readonly_registry(), tmp_path)).handle, port=0)
    await server.start()
    try:
        code, result = await run_cli(server.port, "inspect", steps=1)
        assert code == 1 and result["status"] == "failed" and result["stop_reason"] == "max_steps"
    finally:
        await server.close()


@pytest.mark.parametrize("params", [
    {"goal": " "}, {"goal": "read", "max_steps": 0}, {"goal": "read", "workspace": "outside"},
])
async def test_invalid_agent_rpc_params(params: dict[str, object]) -> None:
    server = SocketServer(CoreService().handle, port=0)
    await server.start()
    try:
        with pytest.raises(RemoteError) as error:
            await request(server.host, server.port, "agent.run", params)
        assert error.value.code == -32602
    finally:
        await server.close()


async def test_unconfigured_model_does_not_break_ping() -> None:
    server = SocketServer(CoreService().handle, port=0)
    await server.start()
    try:
        with pytest.raises(RemoteError) as error:
            await request(server.host, server.port, "agent.run", {"goal": "inspect"})
        assert error.value.code == -32010
        assert (await request(server.host, server.port, "core.ping", {}))["message"] == "pong"
    finally:
        await server.close()


async def test_shutdown_cancels_an_active_model_request(tmp_path: Path) -> None:
    started, ended = asyncio.Event(), asyncio.Event()

    class WaitingProvider:
        async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()

    server = SocketServer(CoreService(Runner(WaitingProvider(), readonly_registry(), tmp_path)).handle, port=0)
    await server.start()
    pending = asyncio.create_task(request(server.host, server.port, "agent.run", {"goal": "inspect"}))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert (await request(server.host, server.port, "core.ping", {}))["message"] == "pong"
        await server.close()
        await asyncio.wait_for(ended.wait(), 1)
        with pytest.raises((ProtocolError, OSError)):
            await pending
    finally:
        await server.close()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_oversized_serialized_result_returns_an_error(tmp_path: Path) -> None:
    provider = FixedResponseProvider([
        ModelResponse(content=[TextBlock(text="\x00" * 12000)], stop_reason="end_turn")
    ])
    server = SocketServer(CoreService(Runner(provider, readonly_registry(), tmp_path)).handle, port=0)
    await server.start()
    try:
        with pytest.raises(RemoteError) as error:
            await request(server.host, server.port, "agent.run", {"goal": "inspect"})
        assert error.value.code == -32002
        assert (await request(server.host, server.port, "core.ping", {}))["message"] == "pong"
    finally:
        await server.close()
