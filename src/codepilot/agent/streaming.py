"""重试只包住同一步的模型读取，工具在成功响应返回后才执行。"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter
from typing import Any

from codepilot.agent.provider import ProviderError
from codepilot.agent.types import Message, ModelResponse, ToolDefinition
from codepilot.core.context import ExecutionContext

RETRY_DELAYS = (0.1, 0.2)

type StreamCall = Callable[
    [Sequence[Message], Sequence[ToolDefinition], Callable[[str], None]], Awaitable[ModelResponse]
]


async def stream_with_retries(
    stream: StreamCall, context: ExecutionContext, messages: Sequence[Message], tools: Sequence[ToolDefinition],
) -> ModelResponse:
    def record(kind: str, **data: Any) -> None:
        context.record(kind, **data)
        if context.recorder:
            context.recorder.trace(kind, context.step, **data)

    step = context.step
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        def on_text(text: str, attempt_number: int = attempt) -> None:
            if context.events:
                context.events.emit("model.delta", step=step, text=text, attempt=attempt_number)

        record("model.attempt.started", attempt=attempt)
        started = perf_counter()
        try:
            response = await stream(messages, tools, on_text)
        except ProviderError as exc:
            retry = exc.retryable and attempt <= len(RETRY_DELAYS)
            record(
                "model.attempt.failed", attempt=attempt, error=str(exc), will_retry=retry,
                elapsed_ms=round((perf_counter() - started) * 1000, 3),
            )
            if not retry:
                raise
            delay = RETRY_DELAYS[attempt - 1]
            if context.events:
                context.events.emit("model.retry", step=step, attempt=attempt + 1, delay_seconds=delay)
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            record("model.attempt.cancelled", attempt=attempt)
            raise
        else:
            record("model.attempt.completed", attempt=attempt, elapsed_ms=round((perf_counter() - started) * 1000, 3))
            return response
    raise AssertionError("Streaming attempts exhausted without a terminal outcome")
