"""获准后才启动；输出有界但持续排空；所有结束路径都清理进程树。"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from codepilot.core.context import ExecutionContext
from codepilot.tools.readonly import resolve_path
from codepilot.tools.registry import PreparedAction, ToolError


class CommandArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    command: str = Field(min_length=1, max_length=8000)
    cwd: str = Field(default=".", min_length=1, max_length=2048)
    timeout_seconds: FiniteFloat = Field(default=30.0, gt=0, le=120)


def shell_command(command: str) -> tuple[list[str], str, str]:
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        script = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n$OutputEncoding = [Console]::OutputEncoding\n$ErrorActionPreference = 'Stop'\n" + command
        if executable is None:
            raise ToolError("powershell_not_available")
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], "PowerShell", script
    executable = shutil.which("bash")
    if executable is None:
        raise ToolError("bash_not_available")
    return [executable, "--noprofile", "--norc", "-c", command], "bash", command


def command_environment() -> dict[str, str]:
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL"}
    environment = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


async def capture(stream: asyncio.StreamReader) -> tuple[str, bool]:
    data = bytearray()
    total = 0
    while chunk := await stream.read(4096):
        total += len(chunk)
        if len(data) < 4096:
            data.extend(chunk[:4096 - len(data)])
    return bytes(data).decode("utf-8", errors="replace"), total > len(data)


async def stop_tree(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "nt":
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.wait_for(process.wait(), 5)
    except (OSError, TimeoutError) as exc:
        raise ToolError(f"command_cleanup_failed: supervisor_pid={process.pid}", stop_run=True) from exc


async def run_command(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str, str, bool]:
    worker = Path(__file__).with_name("command_worker.py")
    process = None
    readers: list[asyncio.Task[tuple[str, bool]]] = []
    try:
        async with asyncio.timeout(timeout):
            options: dict[str, object] = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            # 绕过 Windows venv redirector，持有真正 supervisor 的可终止句柄。
            process = await asyncio.create_subprocess_exec(
                sys._base_executable, str(worker), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=command_environment(), **options,
            )
            line = await asyncio.wait_for(process.stdout.readline(), 5)
            try:
                ready = json.loads(line)
            except ValueError as exc:
                raise ToolError("command_supervisor_unavailable") from exc
            if ready.get("supervisor") != "ready" or ready.get("pid") != process.pid:
                raise ToolError("command_supervisor_unavailable")
            readers = [asyncio.create_task(capture(process.stdout)), asyncio.create_task(capture(process.stderr))]
            process.stdin.write((json.dumps({"argv": argv, "cwd": str(cwd)}, ensure_ascii=True) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            # returncode 先于管道 EOF 可见，避免后台后代持有管道使正常结束等待死锁。
            while process.returncode is None:
                await asyncio.sleep(0.01)
            code = process.returncode
        await stop_tree(process)
        stdout, stderr = await asyncio.wait_for(asyncio.gather(*readers), 5)
        return code, stdout[0], stderr[0], stdout[1] or stderr[1]
    except TimeoutError as exc:
        raise ToolError("command_timeout") from exc
    finally:
        if process is not None:
            cleanup = asyncio.create_task(stop_tree(process))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                if readers:
                    try:
                        await asyncio.wait_for(asyncio.gather(*readers, return_exceptions=True), 5)
                    except TimeoutError:
                        for reader in readers:
                            reader.cancel()
                        await asyncio.gather(*readers, return_exceptions=True)
                        raise ToolError("command_cleanup_failed", stop_run=True)


def prepare_command(args: CommandArgs, context: ExecutionContext) -> PreparedAction:
    if not args.command.strip():
        raise ToolError("empty_command")
    cwd = resolve_path(context.workspace, args.cwd)
    if not cwd.is_dir():
        raise ToolError("command_cwd_missing")
    argv, shell, effective = shell_command(args.command)

    async def execute() -> str:
        if resolve_path(context.workspace, args.cwd) != cwd:
            raise ToolError("command_cwd_changed")
        code, stdout, stderr, truncated = await run_command(argv, cwd, args.timeout_seconds)
        text = f"exit_code={code}\nshell={shell}\nstdout:\n{stdout}\nstderr:\n{stderr}" + ("\n[output truncated]" if truncated else "")
        if code != 0:
            raise ToolError("command_exit_nonzero\n" + text)
        return text

    preview = {"command": args.command, "effective_script": effective, "shell": shell, "executable": argv[0],
               "cwd": str(cwd), "timeout_seconds": args.timeout_seconds, "python_executable": sys.executable}
    return PreparedAction(preview, execute, args.timeout_seconds + 15)
