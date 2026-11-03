import asyncio
import json
from pathlib import Path
from collections.abc import Sequence

import pytest

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition
from codepilot.core.events import RunEvent
from codepilot.core.app import CoreService
from codepilot.core.runner import Runner
from codepilot.protocol.messages import Request
from codepilot.tools.readonly import readonly_registry
from codepilot.tools.readonly import ReadFileArgs
from codepilot.tools.registry import ToolRegistry
from codepilot.core.context import ExecutionContext
from codepilot.agent.types import ToolUse
from codepilot.transport.rpc import ProtocolError, RemoteError, RpcClient
from codepilot.transport.server import SocketServer
from codepilot.transport.peer import current_peer


async def terminal(client: RpcClient, run_id: str) -> RunEvent:
    async with asyncio.timeout(3):
        while True:
            event = await client.next_event()
            if event.run_id == run_id and event.type == "run.finished":
                return event


async def test_one_connection_pairs_out_of_order_responses() -> None:
    async def handler(request: Request) -> dict[str, object]:
        await asyncio.sleep(request.params["delay"])
        return {"label": request.params["label"]}

    server = SocketServer(handler, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    try:
        await client.connect()
        slow = asyncio.create_task(client.call("unit", {"label": "slow", "delay": 0.05}))
        fast = await client.call("unit", {"label": "fast", "delay": 0})
        assert fast == {"label": "fast"} and not slow.done()
        assert await slow == {"label": "slow"}
    finally:
        await client.close()
        await server.close()


async def test_disconnect_fails_all_waiters() -> None:
    ready = asyncio.Event()

    async def handler(request: Request) -> dict[str, object]:
        ready.set()
        await asyncio.Event().wait()

    server = SocketServer(handler, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    await client.connect()
    pending = [asyncio.create_task(client.call("wait", {})) for _ in range(3)]
    try:
        await ready.wait()
        await server.close()
        results = await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 2)
        assert all(isinstance(item, ProtocolError) for item in results)
    finally:
        await client.close()
        await server.close()


class WaitingProvider:
    def __init__(self) -> None:
        self.started, self.ended = asyncio.Event(), asyncio.Event()

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> ModelResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.ended.set()


@pytest.mark.parametrize("mode", ["cancel", "disconnect", "immediate-cancel"])
async def test_owned_run_cancel_and_disconnect(tmp_path: Path, mode: str) -> None:
    provider = WaitingProvider()
    service = CoreService(Runner(provider, readonly_registry(), tmp_path, record_dir=tmp_path / "records"))
    server = SocketServer(service.handle, port=0)
    await server.start()
    client, stranger = RpcClient(server.host, server.port), RpcClient(server.host, server.port)
    try:
        await client.connect()
        await stranger.connect()
        run_id = (await client.call("agent.start", {"goal": "wait"}))["run_id"]
        if mode != "immediate-cancel":
            await asyncio.wait_for(provider.started.wait(), 1)
        with pytest.raises(RemoteError) as error:
            await stranger.call("agent.cancel", {"run_id": run_id})
        assert error.value.code == -32011
        assert (await client.call("core.ping", {}))["message"] == "pong"
        if mode == "disconnect":
            await client.close()
        else:
            assert (await client.call("agent.cancel", {"run_id": run_id}))["cancelled"]
            assert (await terminal(client, run_id)).data["result"]["status"] == "cancelled"
        if provider.started.is_set():
            await asyncio.wait_for(provider.ended.wait(), 1)
    finally:
        await client.close()
        await stranger.close()
        await server.close()
    assert not service._jobs


async def test_event_and_trace_records_are_correlated_and_redacted(tmp_path: Path) -> None:
    provider = FixedResponseProvider([ModelResponse(content=[TextBlock(text="never-log-key")], stop_reason="end_turn", usage={"input_tokens": 3, "output_tokens": 2})])
    events = []
    result = await Runner(provider, readonly_registry(), tmp_path, record_dir=tmp_path / "records", secrets=("never-log-key",)).run("never-log-key", stream=True, on_event=events.append)
    directory = tmp_path / "records" / result.run_id
    raw = (directory / "events.jsonl").read_text(encoding="utf-8") + (directory / "trace.jsonl").read_text(encoding="utf-8")
    assert "never-log-key" not in raw and "[REDACTED]" in raw
    records = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {item["run_id"] for item in records} == {result.run_id}
    assert records[-1]["data"]["usage"]["output_tokens"] == 2
    assert [event.seq for event in events] == list(range(1, len(events) + 1))


async def test_idle_connection_is_kept_but_timeout_fails_pending_call() -> None:
    cancelled = asyncio.Event()

    async def handler(request: Request) -> dict[str, object]:
        if request.method == "slow":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return {"ok": True}

    server = SocketServer(handler, port=0, io_timeout=0.02)
    await server.start()
    client = RpcClient(server.host, server.port, timeout=0.05)
    try:
        await client.connect()
        await asyncio.sleep(0.07)
        assert await client.call("fast", {}) == {"ok": True}
        with pytest.raises(TimeoutError):
            await client.call("slow", {})
        await asyncio.wait_for(cancelled.wait(), 1)
    finally:
        await client.close()
        await server.close()


async def test_recording_failure_has_terminal_error(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.touch()
    provider = FixedResponseProvider([ModelResponse(content=[TextBlock(text="unused")], stop_reason="end_turn")])
    server = SocketServer(CoreService(Runner(provider, readonly_registry(), tmp_path, record_dir=blocked)).handle, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    try:
        await client.connect()
        run_id = (await client.call("agent.start", {"goal": "inspect"}))["run_id"]
        result = (await terminal(client, run_id)).data["result"]
        assert result["status"] == "failed" and result["stop_reason"] == "internal_error"
    finally:
        await client.close()
        await server.close()


async def test_cancel_before_job_first_step_does_not_call_provider(tmp_path: Path) -> None:
    provider = WaitingProvider()
    service = CoreService(Runner(provider, readonly_registry(), tmp_path))

    async def bundle(request: Request) -> dict[str, object]:
        if request.method == "bundle":
            started = await service.handle(Request(jsonrpc="2.0", id="inner-start", method="agent.start", params={"goal": "wait"}))
            await service.handle(Request(jsonrpc="2.0", id="inner-cancel", method="agent.cancel", params=started))
            return started
        return await service.handle(request)

    server = SocketServer(bundle, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    try:
        await client.connect()
        run_id = (await client.call("bundle", {}))["run_id"]
        result = (await terminal(client, run_id)).data["result"]
        assert result["status"] == "cancelled" and result["steps"] == 0
        assert not provider.started.is_set()
    finally:
        await client.close()
        await server.close()


async def test_full_output_queue_disconnects_only_its_connection() -> None:
    async def flood(request: Request) -> dict[str, object]:
        if request.method == "flood":
            peer = current_peer()
            for seq in range(1, 600):
                peer.event(RunEvent(type="model.delta", run_id="a" * 32, seq=seq, step=1, timestamp="test", data={"text": "x"}))
        return {"ok": True}

    server = SocketServer(flood, port=0)
    await server.start()
    client, healthy = RpcClient(server.host, server.port), RpcClient(server.host, server.port)
    try:
        await client.connect()
        await healthy.connect()
        with pytest.raises((ProtocolError, OSError)):
            await client.call("flood", {})
        assert await healthy.call("fast", {}) == {"ok": True}
    finally:
        await client.close()
        await healthy.close()
        await server.close()


async def test_cancel_during_tool_wait_emits_tool_terminal_state(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def wait(args: ReadFileArgs, context: ExecutionContext) -> str:
        started.set()
        await asyncio.Event().wait()

    tools = ToolRegistry()
    tools.register("read_file", "wait", ReadFileArgs, wait)
    provider = FixedResponseProvider([ModelResponse(content=[ToolUse(id="read-a", name="read_file", input={"path": "sample"})], stop_reason="tool_use")])
    events = []
    task = asyncio.create_task(Runner(provider, tools, tmp_path).run("wait", on_event=events.append))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    result = await task
    assert result.status == "cancelled"
    completed = [item for item in events if item.type == "tool.completed"]
    assert completed[-1].data["is_error"] and "取消" in completed[-1].data["content"]
    assert events[-1].type == "run.finished"
