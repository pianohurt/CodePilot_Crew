"""一块回答持续更新，工具详情单独展示；所有界面状态来自协议。"""

import asyncio
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static

from codepilot.core.config import Settings
from codepilot.transport.rpc import RpcClient
from codepilot.tui.state import RunView


class CodePilotApp(App[None]):
    TITLE = "CodePilot Crew · 只读工作台"
    BINDINGS: ClassVar = [Binding("ctrl+q", "quit", "退出", priority=True), Binding("ctrl+c", "cancel_run", "取消运行", priority=True)]
    CSS = """
    Screen { padding: 1 2; }
    #controls { height: 3; }
    #goal { width: 1fr; }
    Button { margin-left: 1; min-width: 10; }
    #status { height: 2; color: $accent; }
    #answer-pane { height: 2fr; border: round $accent; padding: 1; }
    #tools-pane { height: 1fr; border: round $secondary; padding: 1; }
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.client = RpcClient(settings.host, settings.port, timeout=settings.timeout)
        self.views: dict[str, RunView] = {}
        self.active_run: str | None = None
        self._connected = False
        self._launching = False
        self._consumer: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("连接中…", id="status")
        with Horizontal(id="controls"):
            yield Input(placeholder="输入只读任务，例如：读取 README.md 并概括", id="goal")
            yield Button("开始", id="start", variant="primary", disabled=True)
            yield Button("取消", id="cancel", disabled=True)
        with VerticalScroll(id="answer-pane"):
            yield Static("提交目标后显示模型进度与回答。", id="answer", markup=False)
        with VerticalScroll(id="tools-pane"):
            yield Static("工具调用与结果将在这里显示。", id="tools", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        try:
            await self.client.connect()
            await self.client.call("core.ping", {})
            self._connected = True
            self.query_one("#status", Static).update("已连接 · 等待目标")
            self.query_one("#start", Button).disabled = False
            self._consumer = asyncio.create_task(self._consume())
        except Exception:
            self.query_one("#status", Static).update("连接失败，请先启动 Core")

    async def _consume(self) -> None:
        try:
            while True:
                event = await self.client.next_event()
                if event.run_id not in self.views:
                    if len(self.views) >= 32:
                        self.views.pop(next(iter(self.views)))
                    self.views[event.run_id] = RunView(event.run_id)
                self.views[event.run_id].apply(event)
                if event.run_id == self.active_run:
                    self._render_run()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._connected = False
            if self.is_mounted:
                completed = self.views.get(self.active_run)
                self.query_one("#status", Static).update("连接断开 · 已确认结果保留" if completed and completed.result else "连接断开 · 运行结果未确认")
                self.query_one("#start", Button).disabled = True
                self.query_one("#cancel", Button).disabled = True

    def _render_run(self) -> None:
        view = self.views.get(self.active_run)
        if view is None:
            return
        self.query_one("#answer", Static).update(view.text or "等待模型…")
        self.query_one("#tools", Static).update("\n\n".join(view.tools.values()) or "暂无工具调用")
        status = {"running": "运行中", "success": "已完成", "failed": "失败", "cancelled": "已取消"}[view.status]
        self.query_one("#status", Static).update(f"{status} · 步骤 {view.step} · {view.run_id[:8]}" + (f" · {view.result.stop_reason}" if view.result else ""))
        busy = view.result is None
        self.query_one("#goal", Input).disabled = busy
        self.query_one("#start", Button).disabled = busy or self._launching or not self._connected
        self.query_one("#cancel", Button).disabled = not busy

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.start_run()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            await self.start_run()
        else:
            await self.action_cancel_run()

    async def start_run(self) -> None:
        if not self._connected or self._launching:
            return
        previous = self.views.get(self.active_run)
        if previous is not None and previous.result is None:
            return
        goal = self.query_one("#goal", Input).value.strip()
        if not goal:
            self.query_one("#status", Static).update("请先输入目标")
            return
        self._launching = True
        self.query_one("#start", Button).disabled = True
        try:
            response = await self.client.call("agent.start", {"goal": goal})
            self.active_run = response["run_id"]
            self.views.setdefault(self.active_run, RunView(self.active_run, goal=goal))
        except Exception as exc:
            self.query_one("#status", Static).update(f"启动失败：{exc}")
        finally:
            self._launching = False
            if self.active_run is not None:
                self._render_run()
            else:
                self.query_one("#start", Button).disabled = not self._connected

    async def action_cancel_run(self) -> None:
        if self.active_run is None or not self._connected:
            return
        try:
            response = await self.client.call("agent.cancel", {"run_id": self.active_run})
            if response["cancelled"] and self.views[self.active_run].result is None:
                self.query_one("#status", Static).update("取消请求已发送，等待清理确认…")
        except Exception:
            self.query_one("#status", Static).update("取消未确认 · 连接错误")

    async def on_unmount(self) -> None:
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
        await self.client.close()
