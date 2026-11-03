import asyncio
from collections.abc import Sequence
import json
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Static

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolResult, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings
from codepilot.core.runner import Runner
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RemoteError, RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


class InspectProvider(FixedResponseProvider):
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        super().__init__(responses)
        self.exposed: list[set[str]] = []

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> ModelResponse:
        self.exposed.append({tool.name for tool in tools})
        return await super().complete(messages, tools)


def text_response(text: str = "done") -> ModelResponse:
    return ModelResponse(stop_reason="end_turn", content=[TextBlock(text=text)])


def call(name: str, id_: str, **arguments: object) -> ModelResponse:
    return ModelResponse(stop_reason="tool_use", content=[ToolUse(id=id_, name=name, input=arguments)])


def write_skill(root: Path, name: str, body: str, **metadata: object) -> None:
    directory = root / ".codepilot" / "skills"
    directory.mkdir(parents=True, exist_ok=True)
    header = {"name": name, "description": "Test skill", **metadata}
    (directory / f"{name}.md").write_text(f"---\n{json.dumps(header)}\n---\n{body}\n", encoding="utf-8")


@pytest.mark.parametrize("layer", ["project", "user", "builtin"])
async def test_skill_precedence_and_argument_reach_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layer: str) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    if layer in {"project", "user"}:
        write_skill(home, "review", "USER_TEMPLATE $ARGUMENTS", allowed_tools=["read_file"])
    if layer == "project":
        write_skill(workspace, "review", "PROJECT_TEMPLATE $ARGUMENTS", allowed_tools=["list_dir"])
    provider = InspectProvider([text_response()])
    events = []
    runner = Runner(provider, builtin_registry(), workspace)
    result = await runner.run("/skill review source path/模块.py $ARGUMENTS", on_event=events.append)
    expected = {
        "project": ("PROJECT_TEMPLATE", {"list_dir"}),
        "user": ("USER_TEMPLATE", {"read_file"}),
        "builtin": ("审查目标", {"read_file", "list_dir"}),
    }[layer]
    prompt = provider.requests[0][-1].content[0].text
    assert result.status == "success" and expected[0] in prompt
    assert "source path/模块.py $ARGUMENTS" in prompt
    assert provider.exposed == [expected[1]]
    assert events[0].data["skill_source"] == layer


@pytest.mark.parametrize("allowed", [None, []])
async def test_missing_and_empty_whitelists_are_distinct_at_execution(tmp_path: Path, allowed: list[str] | None) -> None:
    metadata = {} if allowed is None else {"allowed_tools": allowed}
    write_skill(tmp_path, "inspect", "Inspect $ARGUMENTS", **metadata)
    (tmp_path / "sample.txt").write_text("ACTUAL_EVIDENCE", encoding="utf-8")
    provider = InspectProvider([call("read_file", "read", path="sample.txt"), text_response()])
    runner = Runner(provider, builtin_registry(), tmp_path)
    result = await runner.run("/skill inspect sample.txt")
    observation = provider.requests[-1][-1].content[0]
    assert isinstance(observation, ToolResult) and result.status == "success"
    if allowed is None:
        assert not observation.is_error and "ACTUAL_EVIDENCE" in observation.content
        assert "read_file" in provider.exposed[0]
    else:
        assert observation.is_error and observation.content == "permission_denied"
        assert provider.exposed == [set(), set()]


@pytest.mark.parametrize("case", ["unknown_tool", "bad_metadata", "missing_placeholder", "traversal"])
async def test_invalid_skill_is_rejected_before_rpc_run_or_session_claim(tmp_path: Path, case: str) -> None:
    if case == "unknown_tool":
        write_skill(tmp_path, "review", "Review $ARGUMENTS", allowed_tools=["mcp__missing__read"])
    elif case == "bad_metadata":
        write_skill(tmp_path, "review", "Review $ARGUMENTS", allowed_tools="read_file")
    elif case == "missing_placeholder":
        write_skill(tmp_path, "review", "Review this", allowed_tools=[])
    goal = "/skill ../review target" if case == "traversal" else "/skill review target"
    provider = InspectProvider([])
    store = SessionStore(tmp_path / ".codepilot", tmp_path)
    runner = Runner(provider, builtin_registry(), tmp_path, sessions=SessionManager(store))
    service = CoreService(runner)
    server = SocketServer(service.handle, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    try:
        await client.connect()
        session = (await client.call("session.create", {}))["id"]
        for method in ("agent.start", "agent.run"):
            with pytest.raises(RemoteError) as exc:
                await client.call(method, {"goal": goal, "session_id": session})
            assert exc.value.code == -32602 and "skill" in str(exc.value).lower()
        assert not service._jobs and not provider.requests
        assert store.summary(session)["run_count"] == 0
    finally:
        await client.close()
        await server.close()
        store.close()


async def test_delegation_cannot_add_tools_excluded_by_skill(tmp_path: Path) -> None:
    write_skill(tmp_path, "delegate", "Investigate $ARGUMENTS", allowed_tools=["spawn_agent", "list_dir"])
    (tmp_path / "sample.txt").write_text("SHOULD_NOT_BE_READ", encoding="utf-8")
    provider = InspectProvider([
        call("spawn_agent", "child", role="planner", goal="Read sample.txt", allowed_tools=["read_file"], acceptance="Report evidence"),
        call("read_file", "blocked", path="sample.txt"),
        text_response("No file access"),
        text_response("done"),
    ])
    result = await Runner(provider, builtin_registry(), tmp_path).run("/skill delegate sample.txt")
    assert result.status == "success"
    assert provider.exposed == [{"spawn_agent", "list_dir"}, set(), set(), {"spawn_agent", "list_dir"}]
    child_observation = provider.requests[2][-1].content[0]
    assert isinstance(child_observation, ToolResult) and child_observation.is_error
    assert child_observation.content == "permission_denied"
    assert "Investigate sample.txt" in provider.requests[0][-1].content[0].text


async def test_tui_submits_explicit_builtin_debug_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    provider = InspectProvider([text_response("Debug result")])
    runner = Runner(provider, builtin_registry(), tmp_path)
    server = SocketServer(CoreService(runner).handle, port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port))
    try:
        async with app.run_test(size=(110, 45)) as pilot:
            async with asyncio.timeout(5):
                while app.query_one("#start", Button).disabled:
                    await asyncio.sleep(0.01)
                app.query_one("#goal", Input).value = "/skill debug sample.py fails with KeyError"
                await pilot.click("#start")
                while app.active_run is None or app.views[app.active_run].result is None:
                    await asyncio.sleep(0.01)
            assert str(app.query_one("#answer", Static).content) == "Debug result"
            status = str(app.query_one("#status", Static).content)
            assert "Skill debug" in status and "上下文估算" in status
        prompt = provider.requests[0][-1].content[0].text
        assert "诊断目标" in prompt and "sample.py fails with KeyError" in prompt
        assert provider.exposed == [{"read_file", "list_dir"}]
    finally:
        await server.close()
