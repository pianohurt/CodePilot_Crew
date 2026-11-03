"""进程内事件分发；网络和记录是订阅方，不让循环依赖界面。"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from codepilot.agent.types import RunResult

type EventName = Literal[
    "run.started", "step.started", "model.started", "model.delta", "model.completed",
    "tool.started", "tool.completed", "run.finished",
    "approval.requested", "approval.resolved",
]


class RunEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    kind: Literal["event"] = "event"
    type: EventName
    run_id: str
    seq: int = Field(ge=1)
    step: int = Field(ge=0)
    tool_use_id: str | None = None
    timestamp: str
    data: dict[str, Any]

    @model_validator(mode="after")
    def check_payload(self) -> "RunEvent":
        if self.type in {"model.delta", "model.completed"} and not isinstance(self.data.get("text"), str):
            raise ValueError("Model text event requires text")
        if self.type.startswith("tool."):
            if not self.tool_use_id or not isinstance(self.data.get("name"), str):
                raise ValueError("Tool event requires a call id and name")
            if self.type == "tool.completed" and (not isinstance(self.data.get("content"), str) or type(self.data.get("is_error")) is not bool):
                raise ValueError("Tool completion requires content and error state")
        if self.type == "run.finished":
            result = RunResult.model_validate(self.data.get("result"))
            if result.run_id != self.run_id:
                raise ValueError("Result belongs to another run")
        if self.type.startswith("approval."):
            if not self.tool_use_id or not isinstance(self.data.get("approval_id"), str):
                raise ValueError("Approval requires approval and tool call ids")
            if self.type == "approval.requested" and (not isinstance(self.data.get("name"), str) or not isinstance(self.data.get("parameters"), dict)):
                raise ValueError("Approval requires actual parameters")
            if self.type == "approval.resolved" and self.data.get("status") not in {"allowed", "denied", "timeout", "cancelled"}:
                raise ValueError("Invalid approval state")
        return self


type EventListener = Callable[[RunEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[EventListener]] = {}

    def subscribe(self, run_id: str, listener: EventListener) -> Callable[[], None]:
        self._listeners.setdefault(run_id, []).append(listener)

        def remove() -> None:
            listeners = self._listeners.get(run_id, [])
            if listener in listeners:
                listeners.remove(listener)
            if not listeners:
                self._listeners.pop(run_id, None)
        return remove

    def publish(self, event: RunEvent) -> None:
        for listener in tuple(self._listeners.get(event.run_id, ())):
            listener(event)


class RunEvents:
    def __init__(self, bus: EventBus, run_id: str) -> None:
        self._bus, self.run_id = bus, run_id
        self.seq = 0

    def emit(
        self, event_type: EventName, *, step: int = 0, tool_use_id: str | None = None, **data: Any
    ) -> None:
        self.seq += 1
        self._bus.publish(RunEvent(
            type=event_type, run_id=self.run_id, seq=self.seq, step=step, tool_use_id=tool_use_id,
            timestamp=datetime.now(UTC).isoformat(), data=data,
        ))
