import asyncio
import ctypes
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from codepilot.agent.types import ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.core.permissions import PermissionPolicy
from codepilot.tools.builtin import builtin_registry
from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import ModelResponse
from codepilot.core.app import CoreService
from codepilot.core.runner import Runner
from codepilot.transport.server import SocketServer
from codepilot.transport.rpc import RpcClient


def command_context(root: Path, decision: str = "allow") -> ExecutionContext:
    return ExecutionContext(workspace=root.resolve(), allowed_tools=frozenset({"bash"}), policy=PermissionPolicy(bash=decision))


def is_running(pid: int) -> bool:
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87, "Unable to establish test process termination"
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))
    except ProcessLookupError:
        return False


async def test_command_output_exit_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEPILOT_CREW_API_KEY", "never-in-child-env")
    (tmp_path / "check.py").write_text("import os\nprint('CHILD_OK')\nassert 'CODEPILOT_CREW_API_KEY' not in os.environ\n", encoding="utf-8")
    registry = builtin_registry()
    result = await registry.execute(ToolUse(id="command", name="bash", input={"command": "python check.py", "timeout_seconds": 5}), command_context(tmp_path))
    assert not result.is_error and "exit_code=0" in result.content and "CHILD_OK" in result.content
    (tmp_path / "check.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    failed = await registry.execute(ToolUse(id="failed", name="bash", input={"command": "python check.py"}), command_context(tmp_path))
    assert failed.is_error and "command_exit_nonzero" in failed.content


@pytest.mark.parametrize("decision", ["deny", "ask"])
async def test_denied_command_does_not_start(tmp_path: Path, decision: str) -> None:
    (tmp_path / "mark.py").write_text("from pathlib import Path\nPath('executed').touch()\n", encoding="utf-8")
    ctx = command_context(tmp_path, decision)

    async def deny(call: ToolUse, preview: dict[str, Any], state: ExecutionContext) -> bool:
        assert preview["command"] == "python mark.py"
        assert preview["cwd"] == str(tmp_path)
        assert preview["shell"] == ("PowerShell" if os.name == "nt" else "bash")
        return False

    ctx.approver = deny
    result = await builtin_registry().execute(ToolUse(id="deny", name="bash", input={"command": "python mark.py"}), ctx)
    assert result.is_error and not (tmp_path / "executed").exists()


async def test_command_output_is_bounded_and_pipes_are_drained(tmp_path: Path) -> None:
    (tmp_path / "output.py").write_text("import sys\nprint('x'*200000)\nprint('y'*200000,file=sys.stderr)\n", encoding="utf-8")
    result = await builtin_registry().execute(ToolUse(id="output", name="bash", input={"command": "python output.py", "timeout_seconds": 10}), command_context(tmp_path))
    assert not result.is_error and "truncated" in result.content
    assert len(result.content.encode()) <= 8192


@pytest.mark.parametrize("arguments", [
    {"command": "python mark.py", "cwd": ".."},
    {"command": "python mark.py", "timeout_seconds": 0},
    {"command": "python mark.py", "timeout_seconds": float("inf")},
    {"command": " "},
])
async def test_invalid_commands_never_request_approval(tmp_path: Path, arguments: dict[str, object]) -> None:
    ctx = command_context(tmp_path, "ask")

    async def unexpected(call: ToolUse, preview: dict[str, Any], state: ExecutionContext) -> bool:
        pytest.fail("Invalid command reached approval")

    ctx.approver = unexpected
    result = await builtin_registry().execute(ToolUse(id="invalid-command", name="bash", input=arguments), ctx)
    assert result.is_error and not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["timeout", "cancel", "normal", "shutdown"])
async def test_ordinary_process_tree_is_gone_after_timeout_or_cancel(tmp_path: Path, mode: str) -> None:
    (tmp_path / "linger.py").write_text("from pathlib import Path\nimport os,time\nPath('grandchild.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n", encoding="utf-8")
    ending = "\nwhile not Path('grandchild.pid').exists(): time.sleep(0.01)\n" if mode == "normal" else "\ntime.sleep(60)\n"
    (tmp_path / "spawn.py").write_text("import os,sys,time,subprocess\nfrom pathlib import Path\nPath('parent.pid').write_text(str(os.getpid()))\nchild=subprocess.Popen([sys.executable,'linger.py'])\nPath('child.pid').write_text(str(child.pid))\n" + ending, encoding="utf-8")
    call = ToolUse(id="tree", name="bash", input={"command": "python spawn.py", "timeout_seconds": 3 if mode == "timeout" else 30})
    server = None
    client = None
    if mode == "shutdown":
        provider = FixedResponseProvider([ModelResponse(content=[call], stop_reason="tool_use")])
        server = SocketServer(CoreService(Runner(provider, builtin_registry(), tmp_path, policy=PermissionPolicy(bash="allow"))).handle, port=0)
        await server.start()
        client = RpcClient(server.host, server.port)
        await client.connect()
        await client.call("agent.start", {"goal": "wait"})
        task = None
    else:
        task = asyncio.create_task(builtin_registry().execute(call, command_context(tmp_path)))
    if mode in {"cancel", "shutdown"}:
        async with asyncio.timeout(5):
            while not (tmp_path / "grandchild.pid").exists():
                await asyncio.sleep(0.02)
        if mode == "shutdown":
            await server.close()
            await client.close()
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    else:
        result = await task
        if mode == "timeout":
            assert result.is_error and "command_timeout" in result.content
        else:
            assert not result.is_error and "exit_code=0" in result.content
    pids = [int((tmp_path / name).read_text()) for name in ("parent.pid", "child.pid", "grandchild.pid")]
    async with asyncio.timeout(3):
        while any(is_running(pid) for pid in pids):
            await asyncio.sleep(0.02)
