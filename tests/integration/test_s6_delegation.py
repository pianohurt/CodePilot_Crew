import asyncio
from collections.abc import Awaitable, Callable, Sequence
import json
from pathlib import Path
from typing import Any

import pytest

from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolResult, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.delegation import RunTree
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.state import RunView


class RoutedProvider:
    def __init__(self, respond: Callable[..., Awaitable[ModelResponse]]) -> None:
        self.respond = respond

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> ModelResponse:
        return await self.respond(messages, {tool.name for tool in tools})


def reply(text: str) -> ModelResponse:
    return ModelResponse(content=[TextBlock(text=text)], stop_reason="end_turn", usage={"input_tokens": 3, "output_tokens": 2})


def call(name: str, id_: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(content=[ToolUse(id=id_, name=name, input=arguments)], stop_reason="tool_use", usage={"input_tokens": 3, "output_tokens": 2})


def spawn(role: str, id_: str, **kwargs: Any) -> ModelResponse:
    return call("spawn_agent", id_, role=role, goal="Inspect sample", constraints="Keep source unchanged",
                acceptance="Cite actual observations", **kwargs)


def observation(messages: Sequence[Message]) -> ToolResult:
    block = messages[-1].content[0]
    assert isinstance(block, ToolResult)
    return block


async def test_foreground_investigation_review_and_session_usage(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("VALUE = 42", encoding="utf-8")
    events = []
    root_steps = 0

    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        nonlocal root_steps
        prompt = messages[0].content[0].text
        if prompt.startswith("Delegated role:"):
            assert tools == {"read_file", "list_dir"} and "ROOT_PRIVATE" not in prompt
            assert "Keep source unchanged" in prompt and "Cite actual observations" in prompt
            if "planner" in prompt:
                if len(messages) == 1:
                    return call("read_file", "read", path="sample.py")
                assert "VALUE = 42" in observation(messages).content
                return reply("sample.py contains VALUE = 42")
            assert len(messages) == 1 and "sample.py contains VALUE = 42" in prompt
            return reply("Review: value confirmed by planner, tests unverified")
        root_steps += 1
        if root_steps == 1:
            return spawn("planner", "planner")
        result = json.loads(observation(messages).content)
        assert result["status"] == "success"
        if root_steps == 2:
            return spawn("reviewer", "review", facts=result["answer"])
        assert "tests unverified" in result["answer"]
        return reply(result["answer"])

    store = SessionStore(tmp_path / ".codepilot", tmp_path)
    try:
        runner = Runner(RoutedProvider(respond), builtin_registry(), tmp_path, record_dir=tmp_path / "runs", sessions=SessionManager(store))
        session_id = store.create()["id"]
        result = await runner.run("ROOT_PRIVATE", session_id=session_id, on_event=events.append)
        assert result.status == "success" and "tests unverified" in result.answer
        assert result.usage == {"input_tokens": 18, "output_tokens": 12}
        assert store.summary(session_id)["usage"] == result.usage
        children = [event for event in events if event.type == "agent.finished"]
        assert [event.data["role"] for event in children] == ["planner", "reviewer"]
        for event in children:
            lines = (tmp_path / "runs" / event.data["child_run_id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()
            assert json.loads(lines[0])["data"]["parent_run_id"] == result.run_id
        view = RunView(result.run_id)
        for event in events:
            view.apply(event)
        assert all("success" in view.tools[f"child:{event.data['child_run_id']}"] for event in children)
        assert events[-1].type == "run.finished"
    finally:
        store.close()


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
async def test_background_result_reflects_actual_state_while_parent_works(tmp_path: Path, outcome: str) -> None:
    (tmp_path / "sample.txt").write_text("evidence", encoding="utf-8")
    entered, release = asyncio.Event(), asyncio.Event()
    steps = 0
    child_id = ""

    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        nonlocal steps, child_id
        if messages[0].content[0].text.startswith("Delegated role:"):
            assert tools == {"read_file", "list_dir"}
            entered.set()
            await release.wait()
            if outcome == "failed":
                return call("read_file", "child-read", path="sample.txt")
            return reply("background evidence")
        steps += 1
        if steps == 1:
            return spawn("executor", "bg", mode="background", max_steps=1)
        if steps == 2:
            state = json.loads(observation(messages).content)
            assert state["status"] == "running"
            child_id = state["run_id"]
            await entered.wait()
            return call("list_dir", "parent-list", path=".")
        if steps == 3:
            assert "sample.txt" in observation(messages).content and not release.is_set()
            return call("agent_result", "poll", run_id=child_id, wait_seconds=0)
        if steps == 4:
            assert json.loads(observation(messages).content)["status"] == "running"
            if outcome != "cancelled":
                release.set()
            return call("agent_result", "collect", run_id=child_id, cancel=outcome == "cancelled")
        block = observation(messages)
        state = json.loads(block.content)
        assert state["status"] == outcome
        assert block.is_error == (outcome != "success")
        if outcome == "failed":
            assert state["stop_reason"] == "max_steps"
        return reply(outcome)

    result = await Runner(RoutedProvider(respond), builtin_registry(), tmp_path, run_timeout=5).run("background")
    assert result.answer == outcome


@pytest.mark.parametrize("policy", ["deny", "ask"])
async def test_executor_inherits_root_write_policy_over_tcp(tmp_path: Path, policy: str) -> None:
    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        child = messages[0].content[0].text.startswith("Delegated role:")
        if len(messages) == 1:
            if child:
                assert ("write_file" in tools) == (policy == "ask")
                assert "spawn_agent" not in tools and "agent_result" not in tools
                return call("write_file", "write", path="sample.txt", content="approved content")
            return spawn("executor", "delegate")
        if child:
            assert observation(messages).is_error == (policy == "deny")
        return reply("done")

    runner = Runner(RoutedProvider(respond), builtin_registry(), tmp_path, policy=PermissionPolicy(write_file=policy))
    server = SocketServer(CoreService(runner).handle, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    approvals = []
    try:
        await client.connect()
        root = (await client.call("agent.start", {"goal": "delegate edit"}))["run_id"]
        async with asyncio.timeout(5):
            while (event := await client.next_event()).type != "run.finished":
                if event.type == "approval.requested":
                    approvals.append(event)
                    assert event.run_id == root and event.data["parameters"]["child_run_id"] != root
                    assert not (tmp_path / "sample.txt").exists()
                    await client.call("approval.respond", {"approval_id": event.data["approval_id"], "allow": True})
        assert event.data["result"]["status"] == "success"
        assert len(approvals) == (policy == "ask")
        assert (tmp_path / "sample.txt").exists() == (policy == "ask")
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("ending", ["finish", "cancel", "timeout", "shutdown"])
async def test_root_terminal_paths_await_background_cleanup(tmp_path: Path, ending: str) -> None:
    started, cleaned = asyncio.Event(), asyncio.Event()
    events = []

    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        if messages[0].content[0].text.startswith("Delegated role:"):
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
        if len(messages) == 1:
            return spawn("planner", "bg", mode="background")
        await started.wait()
        if ending == "finish":
            return reply("Parent ends before child")
        await asyncio.Future()
        raise AssertionError("unreachable")

    runner = Runner(RoutedProvider(respond), builtin_registry(), tmp_path, run_timeout=0.3 if ending == "timeout" else 5)
    if ending == "shutdown":
        server = SocketServer(CoreService(runner).handle, port=0)
        await server.start()
        client = RpcClient(server.host, server.port)
        try:
            await client.connect()
            await client.call("agent.start", {"goal": "parent"})
            await asyncio.wait_for(started.wait(), 2)
            await server.close()
            assert cleaned.is_set()
        finally:
            await client.close()
            await server.close()
        return
    task = asyncio.create_task(runner.run("parent", on_event=events.append))
    await asyncio.wait_for(started.wait(), 2)
    if ending == "cancel":
        task.cancel()
    result = await asyncio.wait_for(task, 2)
    assert cleaned.is_set()
    assert result.stop_reason == {"finish": "end_turn", "cancel": "cancelled", "timeout": "run_timeout"}[ending]
    assert [event.type for event in events[-2:]] == ["agent.finished", "run.finished"]
    assert events[-2].data["status"] == "cancelled"


@pytest.mark.parametrize("limit", ["model", "tool"])
async def test_tree_budgets_apply_across_parent_and_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    count = 0
    events = []
    if limit == "model":
        monkeypatch.setattr(RunTree, "max_model_calls", 2)
    else:
        monkeypatch.setattr(RunTree, "max_tool_calls", 1)

    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        nonlocal count
        count += 1
        if len(messages) > 1:
            return reply("budget observed")
        if messages[0].content[0].text.startswith("Delegated role:"):
            return call("list_dir", "list", path=".")
        return spawn("planner", "child")

    result = await Runner(RoutedProvider(respond), builtin_registry(), tmp_path).run("budget", on_event=events.append)
    child = next(event for event in events if event.type == "agent.finished")
    assert child.data["stop_reason"] == "tree_budget"
    assert count == (2 if limit == "model" else 3)
    assert result.usage["input_tokens"] == count * 3


async def test_capacity_and_foreign_run_id_fail_without_starting_extra_jobs(tmp_path: Path) -> None:
    child_started = asyncio.Event()
    steps = 0
    events = []

    async def respond(messages: Sequence[Message], tools: set[str]) -> ModelResponse:
        nonlocal steps
        if messages[0].content[0].text.startswith("Delegated role:"):
            child_started.set()
            await asyncio.Future()
            raise AssertionError("unreachable")
        steps += 1
        if steps <= 2:
            return spawn("planner", f"child-{steps}", mode="background")
        if steps == 3:
            await child_started.wait()
            return spawn("planner", "excess", mode="background")
        if steps == 4:
            assert observation(messages).content == "delegation_capacity"
            return call("agent_result", "foreign", run_id="0" * 32)
        assert observation(messages).content == "unknown_child_run"
        return reply("limits observed")

    result = await Runner(RoutedProvider(respond), builtin_registry(), tmp_path).run("capacity", on_event=events.append)
    assert result.answer == "limits observed"
    assert len([event for event in events if event.type == "agent.spawned"]) == 2
    assert [event.data["status"] for event in events if event.type == "agent.finished"] == ["cancelled", "cancelled"]
