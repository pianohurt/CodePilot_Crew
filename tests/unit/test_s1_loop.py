import asyncio
from pathlib import Path

import pytest

from codepilot.agent.loop import AgentLoop
from codepilot.agent.provider import FixedResponseProvider, ProviderError
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolResult, ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry


def final(text: str = "done") -> ModelResponse:
    return ModelResponse(content=[TextBlock(text=text)], stop_reason="end_turn")


def call(call_id: str = "read-1", name: str = "read_file", **arguments: object) -> ToolUse:
    return ToolUse(id=call_id, name=name, input=arguments)


async def test_fixed_response_drives_real_read_then_observes_content(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("unique actual content", encoding="utf-8")
    provider = FixedResponseProvider([
        ModelResponse(content=[call(path="sample.txt")], stop_reason="tool_use"), final("scripted answer")
    ])
    result = await Runner(provider, readonly_registry(), tmp_path).run("read sample.txt")
    assert result.status == "success" and result.answer == "scripted answer" and result.steps == 2
    observation = provider.requests[1][-1]
    assert observation.role == "user"
    assert observation.content[0].content == "1: unique actual content"
    assert observation.content[0].tool_use_id == "read-1"
    assert len(result.tool_calls) == 1 and not result.tool_calls[0].is_error


async def test_multi_tool_errors_return_in_call_order(tmp_path: Path) -> None:
    provider = FixedResponseProvider([
        ModelResponse(content=[call("a", "unknown"), call("b", "read_file"), call("c", "list_dir")], stop_reason="tool_use"), final()
    ])
    result = await Runner(provider, readonly_registry(), tmp_path).run("inspect")
    results = provider.requests[1][-1].content
    assert [item.tool_use_id for item in results] == ["a", "b", "c"]
    assert [item.is_error for item in results] == [True, True, False]
    assert result.status == "success"


@pytest.mark.parametrize("reason", ["end_turn", "max_tokens", "refusal", "unknown"])
async def test_inconsistent_or_incomplete_response_does_not_execute(tmp_path: Path, reason: str) -> None:
    provider = FixedResponseProvider([ModelResponse(content=[call(path="sample.txt")], stop_reason=reason)])
    result = await Runner(provider, readonly_registry(), tmp_path).run("inspect")
    assert result.status == "failed" and result.tool_calls == []


@pytest.mark.parametrize("blocks", [[], [call("same", path="a"), call("same", path="b")]])
async def test_missing_or_duplicate_calls_are_not_adopted(tmp_path: Path, blocks: list[ToolUse]) -> None:
    provider = FixedResponseProvider([ModelResponse(content=blocks, stop_reason="tool_use")])
    context = ExecutionContext(workspace=tmp_path, allowed_tools=frozenset({"read_file"}))
    context.messages = [Message(role="user", content=[TextBlock(text="inspect")])]
    result = await AgentLoop(provider, readonly_registry()).run(context, max_steps=1)
    assert result.stop_reason == "invalid_response" and result.tool_calls == []
    assert len(context.messages) == 1


async def test_step_limit_keeps_last_tool_batch_paired(tmp_path: Path) -> None:
    provider = FixedResponseProvider([ModelResponse(content=[call("a", "list_dir")], stop_reason="tool_use")])
    context = ExecutionContext(workspace=tmp_path, allowed_tools=frozenset({"list_dir"}))
    context.messages = [Message(role="user", content=[TextBlock(text="inspect")])]
    result = await AgentLoop(provider, readonly_registry()).run(context, max_steps=1)
    assert result.status == "failed" and result.stop_reason == "max_steps"
    assert context.messages[-2].content[0].id == context.messages[-1].content[0].tool_use_id
    assert isinstance(context.messages[-1].content[0], ToolResult)


async def test_id_cannot_be_reused_on_later_steps(tmp_path: Path) -> None:
    response = ModelResponse(content=[call("a", "list_dir")], stop_reason="tool_use")
    result = await Runner(FixedResponseProvider([response, response]), readonly_registry(), tmp_path).run("inspect")
    assert result.stop_reason == "invalid_response" and len(result.tool_calls) == 1


async def test_call_budget_and_empty_final_answer(tmp_path: Path) -> None:
    many = ModelResponse(content=[call(str(index), "list_dir") for index in range(9)], stop_reason="tool_use")
    assert (await Runner(FixedResponseProvider([many]), readonly_registry(), tmp_path).run("inspect")).stop_reason == "max_tool_calls"
    assert (await Runner(FixedResponseProvider([final(" ")]), readonly_registry(), tmp_path).run("inspect")).stop_reason == "invalid_response"


async def test_provider_failure_and_run_timeout(tmp_path: Path) -> None:
    class FailingProvider:
        async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
            raise ProviderError("controlled failure")

    class SlowProvider:
        async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
            await asyncio.sleep(1)
            return final()

    failure = await Runner(FailingProvider(), readonly_registry(), tmp_path).run("inspect")
    assert failure.status == "failed" and failure.stop_reason == "provider_error"
    timeout = await Runner(SlowProvider(), readonly_registry(), tmp_path, run_timeout=0.01).run("inspect")
    assert timeout.status == "failed" and timeout.stop_reason == "run_timeout"


async def test_shared_runner_does_not_share_conversation(tmp_path: Path) -> None:
    class EchoProvider:
        async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
            assert len(messages) == 1
            await asyncio.sleep(0)
            return final(messages[0].content[0].text)

    runner = Runner(EchoProvider(), readonly_registry(), tmp_path)
    one, two = await asyncio.gather(runner.run("first"), runner.run("second"))
    assert one.answer == "first" and two.answer == "second"
    assert one.run_id != two.run_id
