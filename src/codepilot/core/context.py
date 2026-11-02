"""每个 run 的可变状态，不放进共享 Provider 或服务对象。"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from codepilot.agent.types import Message, ToolReceipt


@dataclass
class ExecutionContext:
    workspace: Path
    allowed_tools: frozenset[str]
    run_id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    used_tool_ids: set[str] = field(default_factory=set)
    receipts: list[ToolReceipt] = field(default_factory=list)
    step: int = 0
