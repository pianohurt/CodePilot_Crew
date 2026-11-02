"""S0 每次命令只进行一次请求；事件和多路复用留到 S2。"""

import asyncio
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from codepilot.protocol.messages import RESPONSE, Failure, Request
from codepilot.transport.framing import (
    MAX_FRAME_BYTES,
    FrameTooLarge,
    close_writer,
    decode_frame,
    read_frame,
    write_frame,
)


class ProtocolError(RuntimeError):
    pass


class RemoteError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Remote error {code}: {message}")
        self.code = code


async def request(
    host: str,
    port: int,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    message = Request(jsonrpc="2.0", id=uuid4().hex, method=method, params=params)
    writer: asyncio.StreamWriter | None = None
    try:
        # 外层预算同时包含连接、写入和读取，不让每一步各自重置总等待时间。
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port, limit=MAX_FRAME_BYTES)
            await write_frame(writer, message.model_dump(), timeout)
            frame = await read_frame(reader, timeout)
            if frame is None:
                raise ProtocolError("Connection closed before a complete response")
            try:
                response = RESPONSE.validate_python(decode_frame(frame))
            except (ValueError, RecursionError, ValidationError) as exc:
                raise ProtocolError("Invalid response envelope") from exc
            if response.id != message.id:
                raise ProtocolError("Response id does not match request")
            if isinstance(response, Failure):
                raise RemoteError(response.error.code, response.error.message)
            return response.result
    except FrameTooLarge as exc:
        raise ProtocolError("Response frame exceeds 64 KiB") from exc
    finally:
        if writer is not None:
            await close_writer(writer)
