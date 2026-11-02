from collections.abc import AsyncIterator

import pytest_asyncio

from codepilot.core.app import dispatch
from codepilot.transport.server import SocketServer


@pytest_asyncio.fixture
async def core_server() -> AsyncIterator[SocketServer]:
    server = SocketServer(dispatch, port=0, io_timeout=0.3)
    await server.start()
    try:
        yield server
    finally:
        await server.close()
