"""读循环独立、请求并发处理，单一写队列保证完整帧不交错。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from codepilot.protocol.messages import (
    FRAME_TOO_LARGE,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PARSE_ERROR,
    Failure,
    Request,
    RpcFault,
    Success,
    failure,
)
from codepilot.transport.framing import (
    IO_TIMEOUT,
    MAX_FRAME_BYTES,
    FrameTooLarge,
    close_writer,
    decode_frame,
    read_frame,
    write_frame,
)
from codepilot.transport.peer import CURRENT_PEER, PeerConnection

logger = logging.getLogger(__name__)
type Handler = Callable[[Request], Awaitable[dict[str, Any]]]


class SocketServer:
    def __init__(
        self,
        handler: Handler,
        *,
        host: str = "127.0.0.1",
        port: int = 7438,
        io_timeout: float = IO_TIMEOUT,
        shutdown_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self._handler = handler
        self._io_timeout = io_timeout
        self._shutdown_timeout = shutdown_timeout
        self._server: asyncio.Server | None = None
        self._closing = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._peers: set[PeerConnection] = set()

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("Core already started")
        self._closing = False
        # 绑定本身才是端口是否可用的最终依据，不先探测再绑定。
        self._server = await asyncio.start_server(
            self._accept, self.host, self.port, limit=MAX_FRAME_BYTES - 1
        )
        self.port = self._server.sockets[0].getsockname()[1]

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing:
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._handle_connection(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._connection_done)

    def _connection_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Connection cleanup failed", exc_info=(type(error), error, error.__traceback__))

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        connection = PeerConnection(writer, self._io_timeout)
        self._peers.add(connection)
        pump = asyncio.create_task(connection.pump())
        logger.debug("Connected peer=%s", peer)
        try:
            while not self._closing:
                try:
                    frame = await read_frame(reader, self._io_timeout, allow_idle=True)
                except FrameTooLarge:
                    await write_frame(writer, failure(None, FRAME_TOO_LARGE, "Frame exceeds 64 KiB").model_dump(), self._io_timeout)
                    break
                if frame is None:
                    break
                try:
                    parsed = Request.model_validate(decode_frame(frame))
                except (ValueError, RecursionError):
                    connection.reply(await self._process(frame))
                    continue
                if parsed.id in connection.requests or len(connection.requests) >= 32:
                    connection.reply(failure(parsed.id, INVALID_REQUEST, "Duplicate id or too many pending requests"))
                    continue
                task = asyncio.create_task(self._serve_request(connection, parsed.id, frame))
                connection.requests[parsed.id] = task
        except (OSError, TimeoutError) as exc:
            logger.info("Connection ended peer=%s reason=%s", peer, type(exc).__name__)
        except Exception:
            logger.exception("Connection failed peer=%s", peer)
        finally:
            try:
                await connection.finish(pump)
                await close_writer(writer)
            finally:
                self._peers.discard(connection)
                self._writers.discard(writer)
                logger.debug("Disconnected peer=%s", peer)

    async def _serve_request(self, connection: PeerConnection, request_id: str, frame: bytes) -> None:
        token = CURRENT_PEER.set(connection)
        try:
            connection.reply(await self._process(frame))
        finally:
            connection.requests.pop(request_id, None)
            CURRENT_PEER.reset(token)

    async def _process(self, frame: bytes) -> Success | Failure:
        try:
            raw = decode_frame(frame)
        except (ValueError, RecursionError):
            return failure(None, PARSE_ERROR, "Invalid UTF-8 JSON")
        try:
            request = Request.model_validate(raw)
        except ValidationError:
            return failure(None, INVALID_REQUEST, "Invalid request envelope")
        logger.info("Request id=%s method=%s", request.id, request.method)
        try:
            result = await self._handler(request)
            return Success(jsonrpc="2.0", id=request.id, result=result)
        except RpcFault as exc:
            logger.info("Request rejected id=%s code=%s", request.id, exc.code)
            return failure(request.id, exc.code, exc.message)
        except Exception:
            logger.exception("Request failed id=%s method=%s", request.id, request.method)
            return failure(request.id, INTERNAL_ERROR, "Internal server error")

    async def close(self) -> None:
        if self._server is None:
            return
        self._closing = True
        self._server.close()
        for connection in tuple(self._peers):
            connection.disconnect()
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._tasks)
        writers = tuple(self._writers)
        for task in tasks:
            task.cancel()
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                # 即使任务在首次运行前被取消，也要等待它对应的连接真正关闭。
                results = await asyncio.gather(
                    *tasks, *(close_writer(writer) for writer in writers), return_exceptions=True
                )
                await self._server.wait_closed()
        except TimeoutError as exc:
            for writer in tuple(self._writers):
                writer.transport.abort()
            raise RuntimeError("Core cleanup exceeded its time budget") from exc
        self._server = None
        self._writers.difference_update(writers)
        errors = [item for item in results if isinstance(item, Exception)]
        if errors:
            raise RuntimeError("Core connection cleanup failed") from errors[0]
