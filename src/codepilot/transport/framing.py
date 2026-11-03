"""一行一帧：只有完整的换行边界才能交给协议层处理。"""

import asyncio
import json
from typing import Any, Never

MAX_FRAME_BYTES = 64 * 1024
IO_TIMEOUT = 5.0
CLOSE_TIMEOUT = 1.0


class FrameTooLarge(ValueError):
    pass


async def read_frame(
    reader: asyncio.StreamReader, timeout: float = IO_TIMEOUT, *, allow_idle: bool = False
) -> bytes | None:
    head = b""
    if allow_idle:
        head = await reader.read(1)
        if not head:
            return None
        if head == b"\n":
            return head
    try:
        async with asyncio.timeout(timeout):
            frame = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError:
        # EOF 前的残片也不是消息，不能触发业务处理。
        return None
    except asyncio.LimitOverrunError as exc:
        raise FrameTooLarge("Frame exceeds 64 KiB") from exc
    frame = head + frame
    if len(frame) > MAX_FRAME_BYTES:
        raise FrameTooLarge("Frame exceeds 64 KiB")
    return frame


def _reject_constant(value: str) -> Never:
    raise ValueError(f"Non-JSON numeric constant: {value}")


def decode_frame(frame: bytes) -> Any:
    return json.loads(frame.decode("utf-8"), parse_constant=_reject_constant)


def encode_frame(message: dict[str, Any]) -> bytes:
    frame = (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(frame) > MAX_FRAME_BYTES:
        raise FrameTooLarge("Frame exceeds 64 KiB")
    return frame


async def write_frame(
    writer: asyncio.StreamWriter, message: dict[str, Any], timeout: float = IO_TIMEOUT
) -> None:
    writer.write(encode_frame(message))
    async with asyncio.timeout(timeout):
        await writer.drain()


async def close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await writer.wait_closed()
    except OSError:
        # 对端重置时仍必须执行本地 close，但无法要求对端正常挥手。
        return
    except TimeoutError:
        writer.transport.abort()
        raise
