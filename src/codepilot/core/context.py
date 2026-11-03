"""每个 run 的可变状态，不放进共享 Provider 或服务对象。"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
from collections.abc import Callable
from typing import Any

from codepilot.agent.types import Message, ToolReceipt
from codepilot.core.events import RunEvents
from codepilot.core.records import RunRecorder
from codepilot.core.permissions import ApprovalCallback, PermissionPolicy


@dataclass
class ExecutionContext:
    workspace: Path
    allowed_tools: frozenset[str]
    run_id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    used_tool_ids: set[str] = field(default_factory=set)
    receipts: list[ToolReceipt] = field(default_factory=list)
    step: int = 0
    events: RunEvents | None = None
    recorder: RunRecorder | None = None
    streaming: bool = False
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    approver: ApprovalCallback | None = None
    session_id: str | None = None
    journal: Callable[[str, dict[str, Any]], None] | None = None
    checkpoint: Callable[[list[Message], list[Message]], None] | None = None
    note_saver: Callable[[str, str], str] | None = None

    def record(self, kind: str, **data: Any) -> None:
        if self.journal:
            self.journal(kind, {"step": self.step, **data})

    def adopt(self, delta: list[Message]) -> None:
        history = self.messages + delta
        if self.checkpoint:
            self.checkpoint(history, delta)
        self.messages = history
