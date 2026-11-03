"""一块回答持续更新，工具详情单独展示；所有界面状态来自协议。"""

import asyncio
import json
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Select, Static

from codepilot.core.config import Settings
from codepilot.transport.rpc import RpcClient
from codepilot.tui.state import RunView
from codepilot.transport.client import RemoteError


class CodePilotApp(App[None]):
    TITLE = "CodePilot Crew · 编程工作台"
    BINDINGS: ClassVar = [
        Binding("f10", "quit", "退出", priority=True),
        # 编辑器可能先截获 Ctrl+Q；页脚提供 F10，并保留原快捷键兼容独立终端。
        Binding("ctrl+q", "quit", "退出", show=False, priority=True),
        Binding("ctrl+c", "cancel_run", "取消运行", priority=True),
    ]
    CSS = """
    Screen { padding: 1 2; }
    #controls { height: 3; }
    #session-controls { height: 3; }
    #sessions { width: 1fr; }
    #session-status { height: 1; }
    #goal { width: 1fr; }
    Button { margin-left: 1; min-width: 10; }
    #status { height: 2; color: $accent; }
    #answer-pane { height: 2fr; border: round $accent; padding: 1; }
    #tools-pane { height: 1fr; border: round $secondary; padding: 1; }
    #approval-pane { display: none; height: 16; border: heavy $warning; padding: 1; }
    #approval-details { height: 1fr; }
    #approval-buttons { height: 3; }
    """

    def __init__(self, settings: Settings, *, session_id: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.client = RpcClient(settings.host, settings.port, timeout=settings.timeout)
        self.views: dict[str, RunView] = {}
        self.active_run: str | None = None
        self._connected = False
        self._launching = False
        self._consumer: asyncio.Task[None] | None = None
        self.session_id = session_id
        self._session_supported = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("连接中…", id="status")
        with Horizontal(id="session-controls"):
            yield Select([], prompt="选择要恢复的会话", id="sessions")
            yield Button("刷新", id="refresh-sessions")
            yield Button("恢复", id="resume-session")
            yield Button("新会话", id="new-session")
        yield Static("", id="session-status", markup=False)
        with Horizontal(id="controls"):
            yield Input(placeholder="输入任务，例如：定位错误、提出修改并运行检查", id="goal")
            yield Button("开始", id="start", variant="primary", disabled=True)
            yield Button("取消", id="cancel", disabled=True)
            yield Button("退出", id="quit")
        with VerticalScroll(id="answer-pane"):
            yield Static("提交目标后显示模型进度与回答。", id="answer", markup=False)
        with VerticalScroll(id="tools-pane"):
            yield Static("工具调用与结果将在这里显示。", id="tools", markup=False)
        with Vertical(id="approval-pane"):
            yield Static("等待你的本次授权 · 请核对完整参数", markup=False)
            with VerticalScroll(id="approval-details"):
                yield Static("", id="approval", markup=False)
            with Horizontal(id="approval-buttons"):
                yield Button("允许本次", id="allow", variant="warning")
                yield Button("拒绝本次", id="deny")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            await self.client.connect()
            await self.client.call("core.ping", {})
            self._connected = True
            try:
                summary = await self.client.call("session.resume", {"session_id": self.session_id}) if self.session_id else await self.client.call("session.create", {})
                self._session_supported = True
                self._select_session(summary)
                await self.refresh_sessions()
            except RemoteError as exc:
                if exc.code != -32020 or self.session_id is not None:
                    raise
                self.query_one("#session-controls", Horizontal).display = False
                self.query_one("#session-status", Static).display = False
            self.query_one("#status", Static).update("已连接 · 等待目标")
            self.query_one("#start", Button).disabled = False
            self._consumer = asyncio.create_task(self._consume())
        except RemoteError:
            if self._connected:
                self._consumer = asyncio.create_task(self._consume())
                await self.refresh_sessions()
                self.query_one("#status", Static).update("会话恢复失败：记录不存在、损坏或正在运行；可选择其他会话或新建")
            else:
                self.query_one("#status", Static).update("连接失败，请先启动 Core")
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
                self.query_one("#allow", Button).disabled = True
                self.query_one("#deny", Button).disabled = True

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
        for control in ("#refresh-sessions", "#resume-session", "#new-session"):
            self.query_one(control, Button).disabled = busy or not self._connected
        self.query_one("#sessions", Select).disabled = busy or not self._connected
        pending = next(iter(view.approvals.values()), None)
        self.query_one("#approval-pane", Vertical).display = pending is not None
        if pending is not None:
            parameters = dict(pending["parameters"])
            body = parameters.pop("content", None)
            script = parameters.pop("effective_script", None)
            text = json.dumps({"tool": pending["name"], **parameters}, ensure_ascii=False, indent=2)
            if body is not None:
                text += "\n\n完整写入内容：\n" + body
            if script is not None:
                text += "\n\n实际执行脚本：\n" + script
            self.query_one("#approval", Static).update(text)
            self.query_one("#allow", Button).disabled = not self._connected
            self.query_one("#deny", Button).disabled = not self._connected

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.start_run()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            await self.start_run()
        elif event.button.id == "cancel":
            await self.action_cancel_run()
        elif event.button.id == "quit":
            await self.action_quit()
        elif event.button.id == "refresh-sessions":
            await self.refresh_sessions()
        elif event.button.id in {"new-session", "resume-session"}:
            await self.change_session(event.button.id == "new-session")
        elif event.button.id in {"allow", "deny"}:
            await self.respond_approval(event.button.id == "allow")

    def _select_session(self, summary: dict) -> None:
        self.session_id = summary["id"]
        self.active_run = None
        self.query_one("#start", Button).disabled = not self._connected
        self.query_one("#goal", Input).disabled = False
        self.query_one("#session-status", Static).update(f"会话 {self.session_id} · {summary['title']} · {summary['status']}")
        result = summary.get("last_result")
        text = result["answer"] if result and result["status"] == "success" else (result.get("error") or "尚无已完成回答") if result else "新会话 · 等待目标"
        self.query_one("#answer", Static).update(text)
        self.query_one("#tools", Static).update("已恢复保存的上下文；不重放旧工具动作。")

    async def refresh_sessions(self) -> None:
        if not self._connected:
            return
        try:
            result = await self.client.call("session.list", {})
            self.query_one("#sessions", Select).set_options([(f"{item['title']} · {item['status']} · {item['id'][:8]}", item["id"]) for item in result["sessions"]])
        except Exception:
            self.query_one("#status", Static).update("会话列表读取失败")

    async def change_session(self, create: bool) -> None:
        previous = self.views.get(self.active_run)
        if not self._connected or self._launching or (previous and previous.result is None):
            return
        selected = self.query_one("#sessions", Select).value
        if not create and selected is Select.BLANK:
            self.query_one("#status", Static).update("请先选择要恢复的会话")
            return
        try:
            summary = await self.client.call("session.create", {}) if create else await self.client.call("session.resume", {"session_id": selected})
            self._select_session(summary)
            await self.refresh_sessions()
            self.query_one("#status", Static).update("会话已就绪 · 等待目标")
        except Exception:
            self.query_one("#status", Static).update("会话不可恢复：可能正在运行或记录损坏")

    async def respond_approval(self, allow: bool) -> None:
        view = self.views.get(self.active_run)
        if view is None or not view.approvals or not self._connected:
            return
        approval_id = next(iter(view.approvals))
        self.query_one("#allow", Button).disabled = True
        self.query_one("#deny", Button).disabled = True
        try:
            await self.client.call("approval.respond", {"approval_id": approval_id, "allow": allow})
        except Exception:
            self.query_one("#status", Static).update("审批未接受或已过期")

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
            params = {"goal": goal}
            if self.session_id:
                params["session_id"] = self.session_id
            response = await self.client.call("agent.start", params)
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
