import asyncio
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from codepilot import get_version
from codepilot.transport.client import ProtocolError, RemoteError, request
from codepilot.transport.framing import MAX_FRAME_BYTES, close_writer, decode_frame, encode_frame, read_frame
from codepilot.transport.server import SocketServer


@asynccontextmanager
async def fake_server(mode: str) -> AsyncIterator[int]:
    tasks: set[asyncio.Task[None]] = set()

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            message = decode_frame(await read_frame(reader))
            response = {"jsonrpc": "2.0", "id": message["id"], "result": {"message": "pong", "server_version": "test"}}
            if mode == "silent":
                await reader.read()
                return
            if mode == "disconnect":
                return
            if mode == "wrong-id":
                response["id"] = "unrelated-id"
            elif mode == "missing-jsonrpc":
                response.pop("jsonrpc")
            elif mode == "both-results":
                response["error"] = {"code": -32603, "message": "failure"}
            elif mode == "remote-error":
                response.pop("result")
                response["error"] = {"code": -32603, "message": "failure"}
            elif mode == "invalid-pong":
                response["result"] = {"message": "not-pong", "server_version": "test"}
            frame = encode_frame(response)
            if mode == "partial-close":
                frame = frame[:-1]
            elif mode == "oversized":
                frame = b"x" * (MAX_FRAME_BYTES + 1) + b"\n"
            elif mode == "invalid-json":
                frame = b"{\n"
            writer.write(frame)
            await writer.drain()
        finally:
            await close_writer(writer)

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.add(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("silent", TimeoutError),
        ("disconnect", ProtocolError),
        ("wrong-id", ProtocolError),
        ("missing-jsonrpc", ProtocolError),
        ("both-results", ProtocolError),
        ("partial-close", ProtocolError),
        ("oversized", ProtocolError),
        ("invalid-json", ProtocolError),
        ("remote-error", RemoteError),
    ],
)
async def test_client_rejects_invalid_or_missing_response(mode: str, error: type[Exception]) -> None:
    async with fake_server(mode) as port:
        with pytest.raises(error):
            await request("127.0.0.1", port, "core.ping", {}, timeout=0.1)


async def run_cli(*args: str, console_entry: bool = False) -> tuple[int, str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CODEPILOT_CREW_")}
    env["PYTHONUTF8"] = "1"
    if console_entry:
        executable = Path(sys.executable).with_name("codepilot.exe" if sys.platform == "win32" else "codepilot")
        command = [str(executable), *args]
    else:
        command = [sys.executable, "-m", "codepilot.cli", *args]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8")


async def test_installed_console_version_without_core() -> None:
    code, stdout, stderr = await run_cli("version", console_entry=True)
    assert (code, stdout.strip(), stderr) == (0, f"codepilot {get_version()}", "")


async def test_cli_ping_uses_real_tcp(core_server: SocketServer) -> None:
    code, stdout, stderr = await run_cli("ping", "--port", str(core_server.port))
    assert (code, stdout.strip(), stderr) == (0, "pong", "")


@pytest.mark.parametrize("mode", ["wrong-id", "silent", "remote-error", "invalid-pong"])
async def test_cli_failure_is_not_reported_as_pong(mode: str) -> None:
    async with fake_server(mode) as port:
        code, stdout, stderr = await run_cli("ping", "--port", str(port), "--timeout", "0.1")
        assert code == 1
        assert stdout == ""
        assert stderr.strip()


async def test_cli_offline_and_occupied_port(core_server: SocketServer) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        code, stdout, stderr = await run_cli("ping", "--port", str(reserved.getsockname()[1]))
        assert code == 1
        assert stdout == ""
        assert "ping failed" in stderr
    code, stdout, stderr = await run_cli("core", "--port", str(core_server.port))
    assert code == 1
    assert "core failed" in stderr
    assert (await request(core_server.host, core_server.port, "core.ping", {}))["message"] == "pong"


@pytest.mark.parametrize("args", [("core", "--port", "0"), ("ping", "--timeout", "nan"), ("ping", "--port", "65536")])
async def test_cli_invalid_configuration_has_exit_code_two(args: tuple[str, ...]) -> None:
    code, stdout, stderr = await run_cli(*args)
    assert code == 2
    assert stdout == ""
    assert "Invalid configuration" in stderr
