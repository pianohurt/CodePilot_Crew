import asyncio
from collections.abc import AsyncIterator, Callable
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from textual.widgets import Input, Static

from codepilot.agent.provider import AnthropicProvider
from codepilot.core.app import CoreService
from codepilot.core.config import Settings
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.tools.builtin import builtin_registry
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.rpc import RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


def frame(kind: str, **data: Any) -> str:
    return "data: " + json.dumps({"type": kind, **data}) + "\n\n"


def prefix(text: str) -> str:
    return (
        frame("message_start", message={"content": []})
        + frame("content_block_start", index=0, content_block={"type": "text", "text": ""})
        + frame("content_block_delta", index=0, delta={"type": "text_delta", "text": text})
    )


def end(reason: str = "end_turn") -> str:
    return frame("content_block_stop", index=0) + frame("message_delta", delta={"stop_reason": reason}) + frame("message_stop")


def write_prefix(content: str) -> str:
    return (
        frame("message_start", message={"content": []})
        + frame("content_block_start", index=0, content_block={
            "type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "sample.txt", "content": content},
        })
    )


class Wire(httpx.AsyncByteStream):
    def __init__(self, data: str, *, broken: bool = False, gate: asyncio.Event | None = None) -> None:
        self.data = data
        self.broken = broken
        self.gate = gate

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.gate is not None:
            await self.gate.wait()
        yield self.data.encode()
        if self.broken:
            raise httpx.ReadError("injected read failure")


@pytest.mark.parametrize("fault", ["connect", "exhausted", "malformed", "http", "cancel"])
async def test_retry_policy_and_terminal_state(tmp_path: Path, fault: str) -> None:
    requests = []
    events = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        if fault == "connect" and len(requests) == 1:
            raise httpx.ConnectError("injected before text", request=request)
        if fault in {"exhausted", "cancel"}:
            return httpx.Response(200, stream=Wire(prefix("discarded"), broken=True))
        if fault == "malformed":
            return httpx.Response(200, stream=Wire("data: [\n\n"))
        if fault == "http":
            return httpx.Response(503)
        return httpx.Response(200, stream=Wire(prefix("accepted") + end()))

    def receive(event: Any) -> None:
        events.append(event)
        if fault == "cancel" and event.type == "model.retry":
            asyncio.current_task().cancel()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(client, base_url="https://test.invalid", api_key="fake", model="fake")
        result = await Runner(provider, readonly_registry(), tmp_path, record_dir=tmp_path / "runs").run(
            "Read only", stream=True, on_event=receive,
        )
    expected = {"connect": 2, "exhausted": 3, "malformed": 1, "http": 1, "cancel": 1}
    assert len(requests) == expected[fault] and len(set(requests)) == 1
    assert not result.tool_calls
    assert result.status == {"connect": "success", "cancel": "cancelled"}.get(fault, "failed")
    if fault == "connect":
        assert result.answer == "accepted"
    else:
        assert not result.answer
    trace = [json.loads(line) for line in (tmp_path / "runs" / result.run_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    started = [item["data"]["attempt"] for item in trace if item["kind"] == "model.attempt.started"]
    assert started == list(range(1, len(requests) + 1))


async def test_retry_never_executes_partial_tool_calls_or_replays_writes(tmp_path: Path) -> None:
    requests = []
    events = []
    streams = [
        Wire(write_prefix("WRONG") + frame("content_block_stop", index=0)),
        Wire(write_prefix("saved once") + end("tool_use")),
        Wire(prefix("discarded"), broken=True),
        Wire(prefix("done") + end()),
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=streams[len(requests) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(client, base_url="https://test.invalid", api_key="fake", model="fake")
        runner = Runner(provider, builtin_registry(), tmp_path, policy=PermissionPolicy(write_file="allow"))
        result = await runner.run("Write sample.txt", stream=True, on_event=events.append)
    assert result.answer == "done" and len(result.tool_calls) == 1 and not result.tool_calls[0].is_error
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "saved once"
    assert requests[0] == requests[1] and requests[2] == requests[3]
    assert sum(event.type == "tool.started" for event in events) == 1


async def eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_mounted_tui_replaces_broken_draft_while_another_run_completes(tmp_path: Path) -> None:
    disconnect, new_answer = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        goal = json.loads(request.content)["messages"][-1]["content"][-1]["text"]
        if goal == "other":
            return httpx.Response(200, stream=Wire(prefix("other answer") + end()))
        attempts += 1
        if attempts == 1:
            class Interrupted(Wire):
                async def __aiter__(self) -> AsyncIterator[bytes]:
                    yield prefix("old draft").encode()
                    await disconnect.wait()
                    raise httpx.ReadError("injected after text")
            return httpx.Response(200, stream=Interrupted(""))
        return httpx.Response(200, stream=Wire(prefix("new answer") + end(), gate=new_answer))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(client, base_url="https://test.invalid", api_key="fake", model="fake")
        server = SocketServer(CoreService(Runner(provider, readonly_registry(), tmp_path)).handle, port=0)
        await server.start()
        app = CodePilotApp(Settings(port=server.port))
        other = RpcClient(server.host, server.port)
        try:
            async with app.run_test(size=(110, 45)) as pilot:
                answer = app.query_one("#answer", Static)
                app.query_one("#goal", Input).value = "retry me"
                await pilot.click("#start")
                await eventually(lambda: str(answer.content) == "old draft")
                await other.connect()
                await other.call("agent.start", {"goal": "other"})
                async with asyncio.timeout(5):
                    while (event := await other.next_event()).type != "run.finished":
                        pass
                assert event.data["result"]["answer"] == "other answer"
                assert str(answer.content) == "old draft"
                disconnect.set()
                await eventually(lambda: attempts == 2 and str(answer.content) == "等待模型…")
                new_answer.set()
                await eventually(lambda: app.views[app.active_run].result is not None)
                assert str(answer.content) == app.views[app.active_run].result.answer == "new answer"
        finally:
            await other.close()
            await server.close()
