import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

import pytest

from codepilot import get_version
from codepilot.core.app import dispatch
from codepilot.protocol.messages import Request
from codepilot.transport.client import RemoteError, request
from codepilot.transport.framing import MAX_FRAME_BYTES, close_writer, decode_frame, encode_frame, read_frame
from codepilot.transport.server import SocketServer


@asynccontextmanager
async def peer(server: SocketServer) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    reader, writer = await asyncio.open_connection(server.host, server.port)
    try:
        yield reader, writer
    finally:
        await close_writer(writer)


async def receive(reader: asyncio.StreamReader) -> dict[str, Any]:
    frame = await read_frame(reader, timeout=1)
    assert frame is not None
    return decode_frame(frame)


async def assert_disconnected(reader: asyncio.StreamReader) -> None:
    try:
        assert await asyncio.wait_for(reader.read(), 1) == b""
    except ConnectionResetError:
        # Windows 关闭仍有未读残片的 TCP 连接时，可能以重置而非 EOF 通知对端。
        pass


def ping_frame(request_id: str = "req-1") -> bytes:
    return encode_frame({"jsonrpc": "2.0", "id": request_id, "method": "core.ping", "params": {}})


async def test_real_tcp_ping(core_server: SocketServer) -> None:
    result = await request(core_server.host, core_server.port, "core.ping", {})
    assert result == {"message": "pong", "server_version": get_version()}


async def test_split_and_coalesced_frames_preserve_ids(core_server: SocketServer) -> None:
    async with peer(core_server) as (reader, writer):
        frame = ping_frame()
        writer.write(frame[:-1])
        await writer.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.03)
        writer.write(frame[-1:] + ping_frame("req-2"))
        await writer.drain()
        assert (await receive(reader))["id"] == "req-1"
        assert (await receive(reader))["id"] == "req-2"


@pytest.mark.parametrize(
    ("bad_frame", "code", "response_id"),
    [
        (b"not json\n", -32700, None),
        (b"\xff\n", -32700, None),
        (b"\r\n", -32700, None),
        (b"[]\n", -32600, None),
        (b'{"jsonrpc":"2.0","method":"core.ping","params":{}}\n', -32600, None),
        (b'{"jsonrpc":"2.0","id":1,"method":"core.ping","params":{}}\n', -32600, None),
        (b'{"jsonrpc":"2.0","id":"a","method":"core.ping","params":{},"extra":1}\n', -32600, None),
        (b'{"jsonrpc":"2.0","id":"a","method":"unknown","params":{}}\n', -32601, "a"),
        (b'{"jsonrpc":"2.0","id":"a","method":"core.ping","params":{"extra":1}}\n', -32602, "a"),
    ],
)
async def test_bad_request_does_not_poison_connection(
    core_server: SocketServer, bad_frame: bytes, code: int, response_id: str | None
) -> None:
    async with peer(core_server) as (reader, writer):
        writer.write(bad_frame + ping_frame())
        await writer.drain()
        error = await receive(reader)
        assert error["error"]["code"] == code
        assert error["id"] == response_id
        assert (await receive(reader))["result"]["message"] == "pong"


async def test_crlf_request_is_accepted(core_server: SocketServer) -> None:
    async with peer(core_server) as (reader, writer):
        writer.write(ping_frame()[:-1] + b"\r\n")
        assert (await receive(reader))["result"]["message"] == "pong"


async def test_partial_disconnect_does_not_dispatch() -> None:
    calls: list[str] = []

    async def handler(message: Request) -> dict[str, Any]:
        calls.append(message.id)
        return await dispatch(message)

    server = SocketServer(handler, port=0)
    await server.start()
    try:
        async with peer(server) as (_, writer):
            writer.write(ping_frame("partial")[:-1])
            await writer.drain()
        async with peer(server) as (reader, writer):
            writer.write(ping_frame("complete"))
            assert (await receive(reader))["result"]["message"] == "pong"
        assert calls == ["complete"]
    finally:
        await server.close()


async def test_slow_and_oversized_clients_are_isolated(core_server: SocketServer) -> None:
    async with peer(core_server) as (reader, writer):
        writer.write(b"{")
        await writer.drain()
        assert (await request(core_server.host, core_server.port, "core.ping", {}))["message"] == "pong"
        await assert_disconnected(reader)
    async with peer(core_server) as (reader, writer):
        writer.write(b"x" * (MAX_FRAME_BYTES + 1))
        await writer.drain()
        assert (await receive(reader))["error"]["code"] == -32001
        await assert_disconnected(reader)
    assert (await request(core_server.host, core_server.port, "core.ping", {}))["message"] == "pong"


async def test_internal_error_is_sanitized() -> None:
    async def broken_handler(message: Request) -> dict[str, Any]:
        raise RuntimeError("private failure detail")

    server = SocketServer(broken_handler, port=0)
    await server.start()
    try:
        with pytest.raises(RemoteError, match="Internal server error") as error:
            await request(server.host, server.port, "core.ping", {})
        assert error.value.code == -32603
        assert "private" not in str(error.value)
    finally:
        await server.close()


async def test_shutdown_closes_clients_and_allows_same_port_restart(core_server: SocketServer) -> None:
    async with peer(core_server) as (reader, writer):
        writer.write(b"{")
        await writer.drain()
        port = core_server.port
        await core_server.close()
        await assert_disconnected(reader)
    replacement = SocketServer(dispatch, port=port)
    await replacement.start()
    try:
        assert (await request(replacement.host, port, "core.ping", {}))["message"] == "pong"
    finally:
        await replacement.close()


async def test_port_in_use_does_not_stop_original(core_server: SocketServer) -> None:
    second = SocketServer(dispatch, port=core_server.port)
    with pytest.raises(OSError):
        await second.start()
    assert (await request(core_server.host, core_server.port, "core.ping", {}))["message"] == "pong"
