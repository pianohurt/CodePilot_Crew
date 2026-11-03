"""单个根 run 拥有其子任务、调用预算与模型用量。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from codepilot.agent.types import RunResult, ToolUse
from codepilot.tools.registry import ToolError

if TYPE_CHECKING:
    from codepilot.core.context import ExecutionContext
    from codepilot.core.runner import Runner
    from codepilot.tools.delegation import SpawnArgs

logger = logging.getLogger(__name__)
READ_ONLY = frozenset({"read_file", "list_dir"})
ROLE_TOOLS = {"planner": READ_ONLY, "executor": READ_ONLY | {"write_file"}, "reviewer": READ_ONLY}
ROLE_PROMPT = {
    "planner": "Investigate inputs and propose concrete steps and checks.",
    "executor": "Perform the delegated change and report which checks actually ran.",
    "reviewer": "Inspect the evidence for defects and unverified claims. Report findings with paths.",
}


@dataclass
class ChildRun:
    run_id: str
    role: str
    mode: str
    task: asyncio.Task[None] | None = None
    result: RunResult | None = None


class RunTree:
    max_model_calls = 64
    max_tool_calls = 48
    max_children = 6
    max_active = 2

    def __init__(self, runner: Runner, root: ExecutionContext) -> None:
        self.runner = runner
        self.root = root
        self.jobs: dict[str, ChildRun] = {}
        self.model_calls = 0
        self.tool_calls = 0
        self.usage: dict[str, int] = {}

    def claim_model(self) -> bool:
        if self.model_calls >= self.max_model_calls:
            return False
        self.model_calls += 1
        return True

    def claim_tools(self, count: int) -> bool:
        if self.tool_calls + count > self.max_tool_calls:
            return False
        self.tool_calls += count
        return True

    def _event(self, kind: str, job: ChildRun, **data: Any) -> None:
        payload = {"child_run_id": job.run_id, "role": job.role, "mode": job.mode, **data}
        self.root.record(kind, **payload)
        if self.root.events:
            self.root.events.emit(kind, step=self.root.step, **payload)

    async def spawn(self, args: SpawnArgs, context: ExecutionContext) -> ChildRun:
        if context is not self.root:
            raise ToolError("delegation_depth_limit")
        if len(self.jobs) >= self.max_children or sum(job.result is None for job in self.jobs.values()) >= self.max_active:
            raise ToolError("delegation_capacity")
        allowed = context.allowed_tools & ROLE_TOOLS[args.role]
        if args.allowed_tools is not None:
            allowed &= frozenset(args.allowed_tools)
        if args.mode == "background":
            allowed &= READ_ONLY
        allowed = frozenset(name for name in allowed if context.policy.decision(name) != "deny")
        job = ChildRun(uuid4().hex, args.role, args.mode)
        self.jobs[job.run_id] = job
        self._event("agent.spawned", job, allowed_tools=sorted(allowed))
        job.task = asyncio.create_task(self._execute(job, args, allowed))
        if args.mode == "foreground":
            await asyncio.shield(job.task)
        return job

    async def query(self, run_id: str, wait_seconds: float, *, cancel: bool = False) -> ChildRun:
        job = self.jobs.get(run_id)
        if job is None:
            raise ToolError("unknown_child_run")
        if job.result is None and job.task is not None:
            if cancel and not job.task.done() and not job.task.cancelling():
                job.task.cancel()
            if wait_seconds:
                await asyncio.wait({job.task}, timeout=wait_seconds)
            if job.task.cancelled() and job.result is None:
                self._finish(job, self._cancelled(job))
        return job

    async def _execute(self, job: ChildRun, args: SpawnArgs, allowed: frozenset[str]) -> None:
        goal = (
            f"Delegated role: {args.role}. {ROLE_PROMPT[args.role]}\n"
            f"Goal: {args.goal}\nInput facts: {args.facts}\nConstraints: {args.constraints}\n"
            f"Available tools: {', '.join(sorted(allowed)) or 'none'}\nAcceptance: {args.acceptance}\n"
            "Report actual evidence and state which acceptance conditions remain unverified."
        )

        async def approve(call: ToolUse, preview: dict[str, Any], _: ExecutionContext) -> bool:
            # 子审批在同一连接的根界面呈现，批准仍绑定这次具体动作。
            return await self.root.approver(call, {**preview, "child_run_id": job.run_id}, self.root)

        try:
            result = await self.runner.run(
                goal, max_steps=args.max_steps, run_id=job.run_id, stream=self.root.streaming,
                approver=approve if self.root.approver is not None else None,
                _parent_tree=self, _allowed_tools=allowed,
            )
        except asyncio.CancelledError:
            result = self._cancelled(job)
        except Exception as exc:
            logger.error("Child run failed run_id=%s error_type=%s", job.run_id, type(exc).__name__)
            result = RunResult(run_id=job.run_id, status="failed", stop_reason="internal_error", steps=0, error="Child run failed")
        self._finish(job, result)

    def _finish(self, job: ChildRun, result: RunResult) -> None:
        job.result = result
        self._event("agent.finished", job, status=result.status, stop_reason=result.stop_reason, usage=result.usage)

    @staticmethod
    def _cancelled(job: ChildRun) -> RunResult:
        return RunResult(run_id=job.run_id, status="cancelled", stop_reason="cancelled", steps=0, error="Parent run ended")

    async def close(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task is not None]
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        try:
            if tasks:
                cleanup = asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
        finally:
            for job in self.jobs.values():
                if job.result is None:
                    self._finish(job, self._cancelled(job))
