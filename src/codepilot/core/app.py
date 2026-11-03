"""Core 装配业务与传输，并拥有其生命周期。"""

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from pydantic import ValidationError

from codepilot import get_version
from codepilot.agent.provider import AnthropicProvider
from codepilot.core.config import ModelSettings, Settings
from codepilot.core.runner import Runner
from codepilot.protocol.messages import INVALID_PARAMS, METHOD_NOT_FOUND, AgentRunParams, CancelParams, PingResult, Request, RpcFault
from codepilot.agent.types import RunResult
from codepilot.core.events import RunEvent
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.server import SocketServer
from codepilot.transport.peer import PeerConnection, current_peer

logger = logging.getLogger(__name__)


async def dispatch(request: Request) -> dict[str, Any]:
    if request.method != "core.ping":
        raise RpcFault(METHOD_NOT_FOUND, "Unknown method")
    if request.params:
        raise RpcFault(INVALID_PARAMS, "core.ping requires empty params")
    return PingResult(message="pong", server_version=get_version()).model_dump()


@dataclass
class OwnedRun:
    owner: PeerConnection
    task: asyncio.Task[None] | None = None
    started: bool = False
    cancel_requested: bool = False


class CoreService:
    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner
        self._jobs: dict[str, OwnedRun] = {}

    async def handle(self, request: Request) -> dict[str, Any]:
        if request.method == "agent.cancel":
            try:
                params = CancelParams.model_validate(request.params)
            except ValueError as exc:
                raise RpcFault(INVALID_PARAMS, "agent.cancel requires run_id") from exc
            job = self._jobs.get(params.run_id)
            if job is None or (job.task is not None and job.task.done()):
                return {"cancelled": False}
            if job.owner is not current_peer():
                raise RpcFault(-32011, "Run belongs to another connection")
            job.cancel_requested = True
            if job.started and job.task is not None:
                job.task.cancel()
            return {"cancelled": True}
        if request.method not in {"agent.run", "agent.start"}:
            return await dispatch(request)
        try:
            params = AgentRunParams.model_validate(request.params)
            if not params.goal.strip():
                raise ValueError("Empty goal")
        except (ValidationError, ValueError) as exc:
            raise RpcFault(INVALID_PARAMS, "agent.run requires a goal and max_steps in 1..32") from exc
        if self._runner is None:
            raise RpcFault(-32010, "Model not configured; restart core with --env-file .env")
        if request.method == "agent.run":
            return (await self._runner.run(params.goal, max_steps=params.max_steps)).model_dump()
        if len(self._jobs) >= 8:
            raise RpcFault(-32012, "Too many active runs")
        owner = current_peer()
        run_id = uuid4().hex
        job = OwnedRun(owner)
        self._jobs[run_id] = job
        job.task = asyncio.create_task(self._run_owned(run_id, job, params))
        owner.own_task(job.task)
        job.task.add_done_callback(lambda task: self._jobs.pop(run_id, None))
        return {"run_id": run_id}

    async def _run_owned(self, run_id: str, job: OwnedRun, params: AgentRunParams) -> None:
        job.started = True
        try:
            await self._runner.run(
                params.goal, max_steps=params.max_steps, run_id=run_id, stream=True,
                on_event=job.owner.event, cancel_before_start=job.cancel_requested,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Run failed run_id=%s error_type=%s", run_id, type(exc).__name__)
            result = RunResult(run_id=run_id, status="failed", stop_reason="internal_error", steps=0, error="Run failed while recording or executing")
            job.owner.event(RunEvent(type="run.finished", run_id=run_id, seq=2**31-1, step=0, timestamp=datetime.now(UTC).isoformat(), data={"result": result.model_dump()}))


async def run_core(
    settings: Settings, *, workspace: Path | None = None, model: ModelSettings | None = None,
    record_dir: Path | None = None,
) -> None:
    async with AsyncExitStack() as resources:
        runner = None
        if model is not None:
            client = await resources.enter_async_context(httpx.AsyncClient(timeout=model.model_timeout))
            provider = AnthropicProvider(
                client, base_url=model.base_url, api_key=model.api_key.get_secret_value(),
                model=model.model, max_tokens=model.max_tokens,
            )
            runner = Runner(
                provider, readonly_registry(), workspace or Path.cwd(),
                record_dir=record_dir or (workspace or Path.cwd()) / "artifacts" / "runs",
                secrets=(model.api_key.get_secret_value(),),
            )
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
