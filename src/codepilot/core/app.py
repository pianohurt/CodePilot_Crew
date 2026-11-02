"""Core 装配业务与传输，并拥有其生命周期。"""

import asyncio
import logging
from typing import Any

from codepilot import get_version
from codepilot.core.config import Settings
from codepilot.protocol.messages import INVALID_PARAMS, METHOD_NOT_FOUND, PingResult, Request, RpcFault
from codepilot.transport.server import SocketServer

logger = logging.getLogger(__name__)


async def dispatch(request: Request) -> dict[str, Any]:
    if request.method != "core.ping":
        raise RpcFault(METHOD_NOT_FOUND, "Unknown method")
    if request.params:
        raise RpcFault(INVALID_PARAMS, "core.ping requires empty params")
    return PingResult(message="pong", server_version=get_version()).model_dump()


async def run_core(settings: Settings) -> None:
    server = SocketServer(dispatch, host=settings.host, port=settings.port)
    await server.start()
    logger.info("Core ready at %s:%s", server.host, server.port)
    try:
        while True:
            # Windows 空闲时也定期唤醒事件循环，让 Ctrl+C 能及时进入 finally。
            await asyncio.sleep(0.2)
    finally:
        await server.close()
        logger.info("Core stopped; connections and listener closed")
