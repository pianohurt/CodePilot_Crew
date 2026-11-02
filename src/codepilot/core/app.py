"""Core 装配业务与传输，并拥有其生命周期。"""

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from codepilot import get_version
from codepilot.agent.provider import AnthropicProvider
from codepilot.core.config import ModelSettings, Settings
from codepilot.core.runner import Runner
from codepilot.protocol.messages import INVALID_PARAMS, METHOD_NOT_FOUND, AgentRunParams, PingResult, Request, RpcFault
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.server import SocketServer

logger = logging.getLogger(__name__)


async def dispatch(request: Request) -> dict[str, Any]:
    if request.method != "core.ping":
        raise RpcFault(METHOD_NOT_FOUND, "Unknown method")
    if request.params:
        raise RpcFault(INVALID_PARAMS, "core.ping requires empty params")
    return PingResult(message="pong", server_version=get_version()).model_dump()


class CoreService:
    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner

    async def handle(self, request: Request) -> dict[str, Any]:
        if request.method != "agent.run":
            return await dispatch(request)
        try:
            params = AgentRunParams.model_validate(request.params)
            if not params.goal.strip():
                raise ValueError("Empty goal")
        except (ValidationError, ValueError) as exc:
            raise RpcFault(INVALID_PARAMS, "agent.run requires a goal and max_steps in 1..32") from exc
        if self._runner is None:
            raise RpcFault(-32010, "Model not configured; restart core with --env-file .env")
        return (await self._runner.run(params.goal, max_steps=params.max_steps)).model_dump()


async def run_core(
    settings: Settings, *, workspace: Path | None = None, model: ModelSettings | None = None
) -> None:
    async with AsyncExitStack() as resources:
        runner = None
        if model is not None:
            client = await resources.enter_async_context(httpx.AsyncClient(timeout=model.model_timeout))
            provider = AnthropicProvider(
                client, base_url=model.base_url, api_key=model.api_key.get_secret_value(),
                model=model.model, max_tokens=model.max_tokens,
            )
            runner = Runner(provider, readonly_registry(), workspace or Path.cwd())
        service = CoreService(runner)
        server = SocketServer(service.handle, host=settings.host, port=settings.port)
        await server.start()
        logger.info("Core ready at %s:%s", server.host, server.port)
        try:
            while True:
                # Windows 空闲时也定期唤醒事件循环，让 Ctrl+C 能及时进入 finally。
                await asyncio.sleep(0.2)
        finally:
            await server.close()
            logger.info("Core stopped; connections and listener closed")
