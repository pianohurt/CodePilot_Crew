"""单连接的写入队列与资源归属；关闭时取消该连接拥有的任务。"""

import asyncio
from contextvars import ContextVar
from typing import Any

from codepilot.core.events import RunEvent
from codepilot.protocol.messages import Failure, Success, failure
from codepilot.transport.framing import FrameTooLarge, encode_frame, write_frame


class PeerConnection:
    def __init__(self, writer: asyncio.StreamWriter, timeout: float) -> None:
        self.writer, self.timeout = writer, timeout
        self.closed = False
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self.requests: dict[str, asyncio.Task[None]] = {}
        self.background: set[asyncio.Task[Any]] = set()

    def own_task(self, task: asyncio.Task[Any]) -> None:
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def disconnect(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.writer.close()
        for task in tuple(self.background):
            if not task.cancelling():
                task.cancel()

    def _enqueue(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            encode_frame(message)
            self.outgoing.put_nowait(message)
        except (FrameTooLarge, asyncio.QueueFull):
            # 无法保证完整交付时明确断连，不把缺失的最终事件悄悄当成功。
            self.disconnect()

    def reply(self, response: Success | Failure) -> None:
        try:
            encode_frame(response.model_dump())
        except FrameTooLarge:
            response = failure(response.id, -32002, "Response exceeds frame limit")
        self._enqueue(response.model_dump())

    def event(self, event: RunEvent) -> None:
        self._enqueue(event.model_dump())

    async def pump(self) -> None:
        try:
            while not self.closed:
                message = await self.outgoing.get()
                await write_frame(self.writer, message, self.timeout)
        except (OSError, TimeoutError):
            self.disconnect()

    async def finish(self, pump: asyncio.Task[None]) -> None:
        self.disconnect()
        tasks = (*self.requests.values(), *self.background, pump)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


CURRENT_PEER: ContextVar[PeerConnection | None] = ContextVar("current_peer", default=None)


def current_peer() -> PeerConnection:
    peer = CURRENT_PEER.get()
    if peer is None:
        raise RuntimeError("This method requires a live connection")
    return peer
