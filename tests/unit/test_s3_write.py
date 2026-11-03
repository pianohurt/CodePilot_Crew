from pathlib import Path
from typing import Any

import pytest

from codepilot.agent.types import ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.events import EventBus, RunEvents
from codepilot.tools.builtin import builtin_registry
from codepilot.tools.registry import ToolError, ToolRegistry
from codepilot.tools.readonly import ReadFileArgs
from codepilot.core.runner import Runner
from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import ModelResponse


def context(root: Path, policy: PermissionPolicy | None = None) -> ExecutionContext:
    return ExecutionContext(workspace=root.resolve(), allowed_tools=frozenset({"read_file", "list_dir", "write_file", "bash"}), policy=policy or PermissionPolicy())


@pytest.mark.parametrize("decision", ["allow", "deny", "ask-allow", "ask-deny", "unavailable"])
async def test_authorization_before_file_effect(tmp_path: Path, decision: str) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("before", encoding="utf-8")
    ctx = context(tmp_path, PermissionPolicy(write_file=decision if decision in {"allow", "deny"} else "ask"))
    events = []
    bus = EventBus()
    bus.subscribe(ctx.run_id, events.append)
    ctx.events = RunEvents(bus, ctx.run_id)

    async def approve(call: ToolUse, preview: dict[str, Any], state: ExecutionContext) -> bool:
        assert path.read_text(encoding="utf-8") == "before"
        assert not any(event.type == "tool.started" for event in events)
        assert preview["content"] == "after" and preview["path"] == str(path)
        return decision == "ask-allow"

    if decision.startswith("ask-"):
        ctx.approver = approve
    result = await builtin_registry().execute(ToolUse(id="write-1", name="write_file", input={"path": "sample.txt", "content": "after", "overwrite": True}), ctx)
    allowed = decision in {"allow", "ask-allow"}
    assert result.is_error != allowed
    assert path.read_text(encoding="utf-8") == ("after" if allowed else "before")
    assert any(event.type == "tool.started" for event in events) == allowed
    assert result.tool_use_id == "write-1"
    assert not list(tmp_path.glob(".codepilot-write-*"))


async def test_edit_after_approval_snapshot_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("before", encoding="utf-8")
    ctx = context(tmp_path)

    async def approve(call: ToolUse, preview: dict[str, Any], state: ExecutionContext) -> bool:
        path.write_text("user concurrent edit", encoding="utf-8")
        return True

    ctx.approver = approve
    result = await builtin_registry().execute(ToolUse(id="conflict", name="write_file", input={"path": "sample.txt", "content": "after", "overwrite": True}), ctx)
    assert result.is_error and "write_conflict" in result.content
    assert path.read_text(encoding="utf-8") == "user concurrent edit"


@pytest.mark.parametrize("arguments", [
    {"path": "../outside.txt", "content": "no"},
    {"path": ".env", "content": "no"},
    {"path": "missing/child.txt", "content": "no"},
    {"path": "sample.txt", "content": "x" * 13000},
    {"path": "sample.txt", "content": 1},
])
async def test_invalid_write_never_asks_or_writes(tmp_path: Path, arguments: dict[str, object]) -> None:
    ctx = context(tmp_path)

    async def reject_unexpected(call: ToolUse, preview: dict[str, Any], state: ExecutionContext) -> bool:
        pytest.fail("Invalid actions must not reach approval")

    ctx.approver = reject_unexpected
    result = await builtin_registry().execute(ToolUse(id="invalid", name="write_file", input=arguments), ctx)
    assert result.is_error and result.tool_use_id == "invalid"
    assert not list(tmp_path.iterdir())


async def test_create_utf8_and_require_explicit_overwrite(tmp_path: Path) -> None:
    ctx = context(tmp_path, PermissionPolicy(write_file="allow"))
    tool = builtin_registry()
    first = await tool.execute(ToolUse(id="new", name="write_file", input={"path": "sample.txt", "content": "汉字 ✅\n"}), ctx)
    assert not first.is_error and (tmp_path / "sample.txt").read_bytes() == "汉字 ✅\n".encode()
    second = await tool.execute(ToolUse(id="again", name="write_file", input={"path": "sample.txt", "content": "replacement"}), ctx)
    assert second.is_error and "overwrite_required" in second.content


async def test_cleanup_failure_stops_later_actions_and_model_requests(tmp_path: Path) -> None:
    async def uncertain(args: ReadFileArgs, state: ExecutionContext) -> str:
        raise ToolError("command_cleanup_failed: supervisor_pid=123", stop_run=True)

    registry = ToolRegistry()
    registry.register("read_file", "controlled failure", ReadFileArgs, uncertain)
    provider = FixedResponseProvider([ModelResponse(content=[ToolUse(id="fatal", name="read_file", input={"path": "first"}), ToolUse(id="never", name="read_file", input={"path": "second"})], stop_reason="tool_use")])
    result = await Runner(provider, registry, tmp_path).run("inspect")
    assert result.status == "failed" and result.stop_reason == "tool_cleanup_failed"
    assert len(result.tool_calls) == 1 and result.tool_calls[0].tool_use_id == "fatal"
    assert len(provider.requests) == 1
