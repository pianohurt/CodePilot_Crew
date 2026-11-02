"""完整响应校验 → 工具批次 → 结果观察；不处理网络与界面。"""

import json
import logging

from codepilot.agent.provider import Provider, ProviderError
from codepilot.agent.types import Message, RunResult, TextBlock, ToolReceipt, ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
MAX_BATCH_CALLS = 8
MAX_RUN_CALLS = 32
MAX_ANSWER_BYTES = 16 * 1024


def finish(
    context: ExecutionContext, reason: str, *, answer: str = "", error: str | None = None
) -> RunResult:
    return RunResult(
        run_id=context.run_id, status="success" if reason == "end_turn" else "failed",
        stop_reason=reason, steps=context.step, answer=answer, error=error,
        tool_calls=[item.model_copy(deep=True) for item in context.receipts],
    )


class AgentLoop:
    def __init__(self, provider: Provider, tools: ToolRegistry) -> None:
        self._provider = provider
        self._tools = tools

    async def run(self, context: ExecutionContext, *, max_steps: int) -> RunResult:
        for step in range(1, max_steps + 1):
            context.step = step
            logger.info("Model request run_id=%s step=%s", context.run_id, step)
            try:
                response = await self._provider.complete(
                    tuple(item.model_copy(deep=True) for item in context.messages),
                    self._tools.definitions(context.allowed_tools),
                )
            except ProviderError as exc:
                reason = "invalid_response" if exc.invalid_response else "provider_error"
                return finish(context, reason, error=str(exc))
            except Exception:
                return finish(context, "provider_error", error="Provider failed unexpectedly")
            calls = [item for item in response.content if isinstance(item, ToolUse)]
            text = "\n".join(item.text for item in response.content if isinstance(item, TextBlock))
            logger.info("Model response run_id=%s step=%s stop_reason=%s", context.run_id, step, response.stop_reason)
            if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
                return finish(context, "invalid_response", error="Model text exceeds S1 limit")
            if response.stop_reason in {"max_tokens", "refusal"}:
                return finish(context, response.stop_reason, error="Model did not finish normally")
            if response.stop_reason == "end_turn":
                if calls or not text.strip():
                    return finish(context, "invalid_response", error="Final response contains calls or no answer")
                context.messages.append(Message(role="assistant", content=response.content))
                return finish(context, "end_turn", answer=text)
            if response.stop_reason != "tool_use":
                return finish(context, "unsupported_stop_reason", error="Unsupported model stop reason")
            ids = [call.id for call in calls]
            if not calls or len(ids) != len(set(ids)) or context.used_tool_ids.intersection(ids):
                return finish(context, "invalid_response", error="Missing calls or repeated tool call id")
            if len(calls) > MAX_BATCH_CALLS or len(context.receipts) + len(calls) > MAX_RUN_CALLS:
                return finish(context, "max_tool_calls", error="Tool call budget exhausted")
            try:
                if any(len(json.dumps(call.input, allow_nan=False).encode()) > 16 * 1024 for call in calls):
                    raise ValueError("Oversized tool input")
            except (ValueError, TypeError, RecursionError):
                return finish(context, "invalid_response", error="Invalid or oversized tool input")
            context.used_tool_ids.update(ids)
            results = []
            for call in calls:
                logger.info("Tool run_id=%s step=%s tool_use_id=%s name=%s", context.run_id, step, call.id, call.name)
                result = await self._tools.execute(call, context)
                results.append(result)
                context.receipts.append(ToolReceipt(step=step, tool_use_id=call.id, name=call.name, is_error=result.is_error))
            # 整批结束后再采用这对消息；总超时或取消不会留下半批工作历史。
            context.messages.extend([
                Message(role="assistant", content=response.content),
                Message(role="user", content=results),
            ])
        return finish(context, "max_steps", error="Model request step budget exhausted")
