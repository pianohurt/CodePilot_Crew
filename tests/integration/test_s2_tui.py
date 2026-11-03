import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Static

from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.server import SocketServer
from codepilot.transport.rpc import RpcClient
from codepilot.tui.app import CodePilotApp


async def eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


class GatedProvider:
    def __init__(self) -> None:
        self.draft, self.release = asyncio.Event(), asyncio.Event()
        self.count = 0

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> ModelResponse:
        return ModelResponse(content=[TextBlock(text="完整回答")], stop_reason="end_turn")

    async def stream_complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition], on_text: Callable[[str], None]) -> ModelResponse:
        self.count += 1
        if self.count == 1:
            return ModelResponse(content=[ToolUse(id="read-a", name="read_file", input={"path": "sample.txt"})], stop_reason="tool_use")
        on_text("草稿")
        self.draft.set()
        await self.release.wait()
        return ModelResponse(content=[TextBlock(text="最终完整回答 ✅")], stop_reason="end_turn")


@pytest.mark.parametrize("mode", ["complete", "cancel", "disconnect", "quit_key", "quit_button"])
async def test_mounted_tui_stream_tools_and_terminal_state(tmp_path: Path, mode: str) -> None:
    (tmp_path / "sample.txt").write_text("actual tool evidence", encoding="utf-8")
    provider = GatedProvider()
    service = CoreService(Runner(provider, readonly_registry(), tmp_path))
    server = SocketServer(service.handle, port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port))
    try:
        async with app.run_test(size=(110, 40)) as pilot:
            await eventually(lambda: not app.query_one("#start", Button).disabled)
            app.query_one("#goal", Input).value = "read sample.txt"
            await pilot.click("#start")
            await asyncio.wait_for(provider.draft.wait(), 2)
            await eventually(lambda: "草稿" in str(app.query_one("#answer", Static).content))
            assert "actual tool evidence" in str(app.query_one("#tools", Static).content)
            assert len(app.query("#answer")) == 1
            assert app.query_one("#goal", Input).disabled
            if mode == "complete":
                provider.release.set()
                await eventually(lambda: app.views[app.active_run].result is not None)
                assert app.views[app.active_run].status == "success"
                assert str(app.query_one("#answer", Static).content) == "最终完整回答 ✅"
                assert "草稿" not in app.views[app.active_run].drafts[2]
            elif mode == "cancel":
                await pilot.press("ctrl+c")
                await eventually(lambda: app.views[app.active_run].result is not None)
                assert app.views[app.active_run].status == "cancelled"
                assert "草稿未采纳" in str(app.query_one("#answer", Static).content)
                assert "已取消" in str(app.query_one("#status", Static).content)
            elif mode == "disconnect":
                await server.close()
                await eventually(lambda: "连接断开" in str(app.query_one("#status", Static).content))
                assert app.query_one("#start", Button).disabled
            else:
                if mode == "quit_key":
                    await pilot.press("f10")
                else:
                    await pilot.click("#quit")
                await eventually(lambda: not app.is_running)
        if mode in {"quit_key", "quit_button"}:
            await eventually(lambda: not service._jobs)
            assert not provider.release.is_set()
            observer = RpcClient(server.host, server.port)
            try:
                await observer.connect()
                assert (await observer.call("core.ping", {}))["message"] == "pong"
            finally:
                await observer.close()
    finally:
        await server.close()


@pytest.mark.parametrize("exit_action", ["f10", "button"])
async def test_tui_connection_failure_stays_usable(exit_action: str) -> None:
    import socket
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        app = CodePilotApp(Settings(port=reserved.getsockname()[1], timeout=0.1))
        async with app.run_test() as pilot:
            assert "连接失败" in str(app.query_one("#status", Static).content)
            assert app.query_one("#start", Button).disabled
            app.query_one("#goal", Input).focus()
            if exit_action == "f10":
                await pilot.press("f10")
            else:
                await pilot.click("#quit")
            await eventually(lambda: not app.is_running)
