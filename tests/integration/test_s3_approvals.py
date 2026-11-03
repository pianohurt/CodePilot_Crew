import asyncio
from pathlib import Path
import os
import sys

import pytest
from textual.widgets import Button, Input, Static

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings
from codepilot.core.runner import Runner
from codepilot.core.permissions import PermissionPolicy
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RemoteError, RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp
from codepilot.core.events import RunEvent


def write_script(content: str = "after") -> FixedResponseProvider:
    return FixedResponseProvider([
        ModelResponse(content=[ToolUse(id="write-a", name="write_file", input={"path": "sample.txt", "content": content, "overwrite": True})], stop_reason="tool_use"),
        ModelResponse(content=[TextBlock(text="scripted final response")], stop_reason="end_turn"),
    ])


async def event_of(client: RpcClient, kind: str) -> RunEvent:
    async with asyncio.timeout(3):
        while True:
            event = await client.next_event()
            if event.type == kind:
                return event


@pytest.mark.parametrize("mode", ["allow", "deny", "timeout", "cancel", "disconnect"])
async def test_approval_lifecycle_and_connection_ownership(tmp_path: Path, mode: str) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("before", encoding="utf-8")
    provider = write_script()
    service = CoreService(Runner(provider, builtin_registry(), tmp_path))
    service.approvals.timeout = 0.1 if mode == "timeout" else 3
    server = SocketServer(service.handle, port=0)
    await server.start()
    client, stranger = RpcClient(server.host, server.port), RpcClient(server.host, server.port)
    try:
        await client.connect()
        await stranger.connect()
        run_id = (await client.call("agent.start", {"goal": "edit"}))["run_id"]
        approval = await event_of(client, "approval.requested")
        assert approval.tool_use_id == "write-a"
        assert approval.data["parameters"]["content"] == "after"
        assert target.read_text(encoding="utf-8") == "before"
        with pytest.raises(RemoteError) as foreign:
            await stranger.call("approval.respond", {"approval_id": approval.data["approval_id"], "allow": True})
        assert foreign.value.code == -32011
        if mode in {"allow", "deny"}:
            assert (await client.call("approval.respond", {"approval_id": approval.data["approval_id"], "allow": mode == "allow"}))["accepted"]
        elif mode == "cancel":
            await client.call("agent.cancel", {"run_id": run_id})
        elif mode == "disconnect":
            await client.close()
        if mode != "disconnect":
            finished = await event_of(client, "run.finished")
            if mode == "cancel":
                assert finished.data["result"]["status"] == "cancelled"
            else:
                observation = provider.requests[1][-1].content[0]
                assert observation.tool_use_id == "write-a"
                assert observation.is_error == (mode != "allow")
                if mode == "timeout":
                    assert "approval_timeout" in observation.content
            with pytest.raises(RemoteError) as stale:
                await client.call("approval.respond", {"approval_id": approval.data["approval_id"], "allow": True})
            assert stale.value.code == -32013
    finally:
        await client.close()
        await stranger.close()
        await server.close()
    assert target.read_text(encoding="utf-8") == ("after" if mode == "allow" else "before")
    assert not service.approvals._pending and not service._jobs


@pytest.mark.parametrize("allow", [True, False])
async def test_mounted_tui_shows_full_preview_and_accepts_once(tmp_path: Path, allow: bool) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("before", encoding="utf-8")
    content = "HEADER\n" + "long preview line\n" * 100 + "END_OF_APPROVAL"
    events = []
    provider = write_script(content)
    runner = Runner(provider, builtin_registry(), tmp_path)
    service = CoreService(runner)
    server = SocketServer(service.handle, port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port))
    try:
        async with app.run_test(size=(110, 50)) as pilot:
            app.query_one("#goal", Input).value = "edit"
            await pilot.click("#start")
            async with asyncio.timeout(3):
                while not app.active_run or not app.views[app.active_run].approvals:
                    await asyncio.sleep(0.01)
            view = app.views[app.active_run]
            remove = runner.events.subscribe(view.run_id, events.append)
            preview = str(app.query_one("#approval", Static).content)
            assert "HEADER" in preview and "END_OF_APPROVAL" in preview
            assert target.read_text(encoding="utf-8") == "before"
            assert not any(event.type == "tool.started" for event in events)
            await pilot.click("#allow" if allow else "#deny")
            async with asyncio.timeout(3):
                while view.result is None:
                    await asyncio.sleep(0.01)
            remove()
            assert target.read_text(encoding="utf-8") == (content if allow else "before")
            assert any(event.type == "tool.started" for event in events) == allow
            assert not view.approvals
            if allow:
                resolved = next(event for event in events if event.type == "approval.resolved")
                started = next(event for event in events if event.type == "tool.started")
                assert resolved.seq < started.seq
    finally:
        await server.close()


@pytest.mark.parametrize("exit_action", ["f10", "button"])
async def test_tui_exit_during_approval_leaves_file_unchanged(tmp_path: Path, exit_action: str) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("before", encoding="utf-8")
    provider = write_script()
    service = CoreService(Runner(provider, builtin_registry(), tmp_path))
    server = SocketServer(service.handle, port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port))
    try:
        async with app.run_test(size=(110, 50)) as pilot:
            app.query_one("#goal", Input).value = "edit"
            await pilot.click("#start")
            async with asyncio.timeout(3):
                while not service.approvals._pending or not app.active_run or not app.views[app.active_run].approvals:
                    await asyncio.sleep(0.01)
            if exit_action == "f10":
                await pilot.press("f10")
            else:
                await pilot.click("#quit")
            async with asyncio.timeout(3):
                while app.is_running:
                    await asyncio.sleep(0.01)
        async with asyncio.timeout(3):
            while service._jobs or service.approvals._pending:
                await asyncio.sleep(0.01)
        assert target.read_text(encoding="utf-8") == "before"
        assert len(provider.requests) == 1
    finally:
        await server.close()


async def test_legacy_cli_path_cannot_silently_approve(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("before", encoding="utf-8")
    provider = write_script()
    result = await Runner(provider, builtin_registry(), tmp_path).run("edit")
    assert result.tool_calls[0].is_error
    assert "approval_unavailable" in provider.requests[1][-1].content[0].content
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "before"


async def test_invalid_policy_is_a_configuration_error(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text('{"write_file":', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("CODEPILOT_CREW_")}
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "codepilot.cli", "core", "--policy-file", str(policy), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 2 and not stdout
    assert b"Invalid configuration" in stderr and b"Traceback" not in stderr
