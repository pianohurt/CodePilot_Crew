"""单次调用兼容入口，实际由多路复用客户端完成。"""

from typing import Any

from codepilot.transport.rpc import ProtocolError, RemoteError, RpcClient


async def request(
    host: str, port: int, method: str, params: dict[str, Any], *, timeout: float = 5.0
) -> dict[str, Any]:
    client = RpcClient(host, port, timeout=timeout)
    try:
        await client.connect()
        return await client.call(method, params)
    finally:
        await client.close()
