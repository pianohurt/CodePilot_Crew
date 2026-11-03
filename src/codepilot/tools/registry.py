"""模型只提出调用；注册表负责授权、参数、超时与结果配对。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from codepilot.agent.types import ToolDefinition, ToolResult, ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.core.permissions import ApprovalError

MAX_TOOL_OUTPUT_BYTES = 8192


def bounded_text(text: str, limit: int = MAX_TOOL_OUTPUT_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    suffix = "\n[truncated]"
    return encoded[:limit - len(suffix.encode())].decode("utf-8", errors="ignore") + suffix


class ToolError(ValueError):
    def __init__(self, message: str, *, stop_run: bool = False) -> None:
        super().__init__(message)
        self.stop_run = stop_run


@dataclass
class PreparedAction:
    preview: dict[str, Any]
    invoke: Callable[[], Awaitable[str]]
    timeout: float | None


@dataclass
class RegisteredTool:
    definition: ToolDefinition
    validate: Callable[[dict[str, Any]], tuple[Any, dict[str, Any]]]
    prepare: Callable[[Any, ExecutionContext], PreparedAction]


class ToolRegistry:
    def __init__(self, *, timeout: float = 2.0) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._timeout = timeout

    def register[T: BaseModel](
        self, name: str, description: str, parameters: type[T],
        handler: Callable[[T, ExecutionContext], Awaitable[str]] | None = None,
        *, prepare: Callable[[T, ExecutionContext], PreparedAction] | None = None,
    ) -> None:
        if handler is None and prepare is None:
            raise ValueError("Tool requires a handler or preparer")
        definition = ToolDefinition(name=name, description=description, input_schema=parameters.model_json_schema())

        def validate(raw: dict[str, Any]) -> tuple[T, dict[str, Any]]:
            parsed = parameters.model_validate(raw)
            return parsed, parsed.model_dump()

        def action(parsed: T, context: ExecutionContext) -> PreparedAction:
            if prepare is not None:
                return prepare(parsed, context)
            assert handler is not None

            async def invoke() -> str:
                return await handler(parsed, context)

            return PreparedAction({"input": parsed.model_dump()}, invoke, self._timeout)

        self._register(RegisteredTool(definition, validate, action))

    def register_external(
        self, definition: ToolDefinition, check: Callable[[dict[str, Any]], None],
        prepare: Callable[[dict[str, Any], ExecutionContext], PreparedAction],
    ) -> None:
        def validate(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            check(raw)
            return raw, raw

        self._register(RegisteredTool(definition, validate, prepare))

    def _register(self, tool: RegisteredTool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Duplicate tool: {name}")
        self._tools[name] = tool

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def definitions(self, allowed: frozenset[str]) -> list[ToolDefinition]:
        return [tool.definition.model_copy(deep=True) for name, tool in self._tools.items() if name in allowed]

    async def execute(self, call: ToolUse, context: ExecutionContext) -> ToolResult:
        error: str | None = None
        content = ""
        stop_run = False
        tool = self._tools.get(call.name)
        if tool is None:
            error = "unknown_tool"
        elif call.name not in context.allowed_tools:
            error = "permission_denied"
        else:
            try:
                parameters, inputs = tool.validate(call.input)
                decision = context.policy.decision(call.name)
                if decision == "deny":
                    raise ApprovalError("permission_denied")
                action = tool.prepare(parameters, context)
                if decision == "ask":
                    if context.approver is None:
                        raise ApprovalError("approval_unavailable: use the TUI or an explicit server policy")
                    if not await context.approver(call, action.preview, context):
                        raise ApprovalError("approval_denied")
                if context.events:
                    context.events.emit("tool.started", step=context.step, tool_use_id=call.id, name=call.name, input=inputs)
                async with asyncio.timeout(action.timeout):
                    content = await action.invoke()
            except ValidationError as exc:
                fields = ", ".join(".".join(map(str, item["loc"])) for item in exc.errors())
                error = f"invalid_arguments: {fields}"
            except ToolError as exc:
                error = str(exc)
                stop_run = exc.stop_run
            except ApprovalError as exc:
                error = str(exc)
            except TimeoutError:
                error = "tool_timeout"
            except FileNotFoundError:
                error = "file_not_found"
            except PermissionError:
                error = "permission_denied"
            except UnicodeDecodeError:
                error = "file_is_not_utf8"
            except OSError:
                error = "filesystem_error"
            except Exception:
                error = "tool_execution_error"
        return ToolResult(tool_use_id=call.id, content=bounded_text(error if error else content), is_error=error is not None, stop_run=stop_run)
