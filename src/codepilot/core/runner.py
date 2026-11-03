"""运行入口装配独立上下文，依赖可共享、消息不可共享。"""

import asyncio
import re
from pathlib import Path
from uuid import uuid4

from codepilot.agent.loop import AgentLoop, finish
from codepilot.agent.provider import Provider
from codepilot.agent.types import Message, RunResult, TextBlock
from codepilot.core.context import ExecutionContext
from codepilot.core.events import EventBus, EventListener, RunEvents
from codepilot.core.records import RunRecorder
from codepilot.core.permissions import ApprovalCallback, PermissionPolicy
from codepilot.tools.registry import ToolRegistry
from codepilot.session.history import validate_history
from codepilot.session.manager import SessionManager, SessionTurn
from codepilot.session.store import SessionError


class Runner:
    def __init__(
        self, provider: Provider, tools: ToolRegistry, workspace: Path, *, run_timeout: float = 90.0,
        record_dir: Path | None = None, secrets: tuple[str, ...] = (),
        policy: PermissionPolicy | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory")
        self._tools = tools
        self._loop = AgentLoop(provider, tools)
        self._run_timeout = run_timeout
        self.events = EventBus()
        self._record_dir, self._secrets = record_dir, secrets
        self._policy = policy or PermissionPolicy()
        self.sessions = sessions

    async def run(
        self, goal: str, *, max_steps: int = 8, run_id: str | None = None,
        on_event: EventListener | None = None, stream: bool = False,
        cancel_before_start: bool = False,
        approver: ApprovalCallback | None = None,
        session_id: str | None = None, turn: SessionTurn | None = None,
    ) -> RunResult:
        if not goal.strip() or len(goal) > 8000:
            raise ValueError("Goal must contain 1 to 8000 characters")
        if type(max_steps) is not int or not 1 <= max_steps <= 32:
            raise ValueError("max_steps must be an integer from 1 to 32")
        run_id = run_id or uuid4().hex
        if re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
            raise ValueError("Invalid run id")
        if turn is None and session_id is not None:
            if self.sessions is None:
                raise SessionError("Sessions not configured", -32020)
            turn = self.sessions.begin(session_id, run_id, goal)
        context = ExecutionContext(workspace=self.workspace, allowed_tools=self._tools.names, run_id=run_id, streaming=stream)
        context.policy, context.approver = self._policy, approver
        if turn is not None:
            context.session_id = turn.session_id
            context.messages = [item.model_copy(deep=True) for item in turn.history]
            context.used_tool_ids = validate_history(context.messages)
            context.journal = lambda kind, data: self.sessions.store.journal(run_id, kind, data)
            context.checkpoint = lambda history, delta: self.sessions.store.checkpoint(turn.session_id, run_id, history, delta)
            context.note_saver = lambda key, content: self.sessions.save_note(turn, key, content)
        else:
            context.allowed_tools = context.allowed_tools - {"note_save"}
        context.events = RunEvents(self.events, run_id)
        remove_record = lambda: None
        remove_event = lambda: None
        try:
            if self._record_dir is not None:
                context.recorder = RunRecorder(self._record_dir, run_id, self._secrets)
                remove_record = self.events.subscribe(run_id, context.recorder.event)
            if on_event is not None:
                remove_event = self.events.subscribe(run_id, on_event)
            try:
                context.adopt([self.sessions.input_message(turn, goal) if turn else Message(role="user", content=[TextBlock(text=goal)])])
                context.events.emit("run.started", goal=goal, session_id=context.session_id)
                async with asyncio.timeout(self._run_timeout):
                    result = finish(context, "cancelled", error="Run cancelled before execution") if cancel_before_start else await self._loop.run(context, max_steps=max_steps)
            except TimeoutError:
                result = finish(context, "run_timeout", error="Run time budget exhausted")
            except asyncio.CancelledError:
                result = finish(context, "cancelled", error="Run cancelled")
            except (SessionError, OSError, UnicodeError, ValueError):
                result = finish(context, "session_error", error="Session/context storage failed; inspect saved state before continuing")
            if turn:
                self.sessions.finish(turn, result, context.messages)
            context.events.emit("run.finished", step=context.step, result=result.model_dump())
            return result
        except Exception:
            if turn and turn.session_id in self.sessions._active:
                self.sessions.finish(turn, finish(context, "internal_error", error="Run failed while recording or executing"), context.messages)
            raise
        finally:
            remove_event()
            remove_record()
            if turn:
                self.sessions._active.discard(turn.session_id)
