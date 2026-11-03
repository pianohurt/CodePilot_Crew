"""一条读循环分流响应与事件；断连使全部等待者有界失败。"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from codepilot.core.events import RunEvent
from codepilot.protocol.messages import RESPONSE, Failure, Request
from codepilot.transport.framing import MAX_FRAME_BYTES, close_writer, decode_frame, read_frame, write_frame


class ProtocolError(RuntimeError):
    pass


class RemoteError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Remote error {code}: {message}")
        self.code = code


class RpcClient:
    def __init__(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[RunEvent | Exception] = asyncio.Queue(maxsize=1024)
        self._write_lock = asyncio.Lock()
        self._error: ProtocolError | None = None

    async def connect(self) -> None:
        async with asyncio.timeout(self.timeout):
            reader, self._writer = await asyncio.open_connection(self.host, self.port, limit=MAX_FRAME_BYTES - 1)
        self._reader_task = asyncio.create_task(self._read_loop(reader))

    def _fail(self, message: str) -> None:
        if self._error is not None:
            return
        self._error = ProtocolError(message)
        if self._writer is not None:
            self._writer.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(self._error)
        while not self._events.empty():
            self._events.get_nowait()
        self._events.put_nowait(self._error)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                frame = await read_frame(reader, self.timeout, allow_idle=True)
                if frame is None:
                    raise ProtocolError("Connection closed before a complete response")
                raw = decode_frame(frame)
                if isinstance(raw, dict) and raw.get("kind") == "event":
                    self._events.put_nowait(RunEvent.model_validate(raw))
                else:
                    response = RESPONSE.validate_python(raw)
                    future = self._pending.get(response.id)
                    if future is None or future.done():
                        raise ProtocolError("Response id does not match request")
                    if isinstance(response, Failure):
                        future.set_exception(RemoteError(response.error.code, response.error.message))
                    else:
                        future.set_result(response.result)
        except asyncio.CancelledError:
            raise
        except ProtocolError as exc:
            self._fail(str(exc))
        except Exception:
            self._fail("Invalid response or connection lost")

    async def call(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        if self._writer is None:
            raise ProtocolError("Client is not connected")
        message = Request(jsonrpc="2.0", id=uuid4().hex, method=method, params=params)
        future = asyncio.get_running_loop().create_future()
        self._pending[message.id] = future
        try:
            async with asyncio.timeout(self.timeout if timeout is None else timeout):
                async with self._write_lock:
                    await write_frame(self._writer, message.model_dump(), self.timeout)
                return await future
        except (TimeoutError, asyncio.CancelledError):
            self._fail("Request interrupted; connection closed")
            raise
        finally:
            self._pending.pop(message.id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def next_event(self) -> RunEvent:
        if self._error is not None:
            raise self._error
        item = await self._events.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self._fail("Connection closed")
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self._writer is not None:
            await close_writer(self._writer)
