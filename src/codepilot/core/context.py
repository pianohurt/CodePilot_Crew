"""每个 run 的可变状态，不放进共享 Provider 或服务对象。"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from codepilot.agent.types import Message, ToolReceipt
from codepilot.core.events import RunEvents
from codepilot.core.records import RunRecorder
from codepilot.core.permissions import ApprovalCallback, PermissionPolicy

if TYPE_CHECKING:
    from codepilot.core.delegation import RunTree


class RunBudgetError(RuntimeError):
    pass


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
    tree: "RunTree | None" = None
    usage: dict[str, int] = field(default_factory=dict)

    def claim_model(self) -> None:
        if self.tree is not None and not self.tree.claim_model():
            raise RunBudgetError("Run tree model request budget exhausted")

    def record(self, kind: str, **data: Any) -> None:
        if kind in {"model.response", "context.summary.response"}:
            for key, value in data["response"].get("usage", {}).items():
                self.usage[key] = self.usage.get(key, 0) + value
                if self.tree is not None:
                    self.tree.usage[key] = self.tree.usage.get(key, 0) + value
        if self.journal:
            self.journal(kind, {"step": self.step, **data})

    def adopt(self, delta: list[Message]) -> None:
        history = self.messages + delta
        if self.checkpoint:
            self.checkpoint(history, delta)
        self.messages = history
