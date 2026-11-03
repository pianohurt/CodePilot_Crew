"""业务领域消息，不向循环泄漏 HTTP 或第三方 SDK 类型。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type CallId = Annotated[str, Field(min_length=1, max_length=128)]


class StrictData(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class TextBlock(StrictData):
    type: Literal["text"] = "text"
    text: str


class ThinkingBlock(StrictData):
    # 兼容端点要求回传的 opaque 数据；循环不解释、展示或据此执行动作。
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str


class ToolUse(StrictData):
    type: Literal["tool_use"] = "tool_use"
    id: CallId
    name: Annotated[str, Field(min_length=1, max_length=64)]
    input: dict[str, Any]


class ToolResult(StrictData):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: CallId
    content: str
    is_error: bool = False
    stop_run: bool = Field(default=False, exclude=True)


type AssistantBlock = Annotated[TextBlock | ThinkingBlock | ToolUse, Field(discriminator="type")]
type ContentBlock = Annotated[TextBlock | ThinkingBlock | ToolUse | ToolResult, Field(discriminator="type")]


class Message(StrictData):
    role: Literal["user", "assistant"]
    content: Annotated[list[ContentBlock], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_role(self) -> Message:
        if self.role == "assistant" and any(isinstance(item, ToolResult) for item in self.content):
            raise ValueError("Assistant cannot supply tool results")
        if self.role == "user" and any(isinstance(item, (ToolUse, ThinkingBlock)) for item in self.content):
            raise ValueError("User cannot supply model-only blocks")
        return self


class ModelResponse(StrictData):
    content: list[AssistantBlock]
    stop_reason: str
    usage: dict[str, int] = Field(default_factory=dict)


class ToolDefinition(StrictData):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolReceipt(StrictData):
    step: int
    tool_use_id: CallId
    name: str
    is_error: bool


class RunResult(StrictData):
    run_id: str
    status: Literal["success", "failed", "cancelled"]
    stop_reason: str
    steps: int
    answer: str = ""
    error: str | None = None
    tool_calls: list[ToolReceipt] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
