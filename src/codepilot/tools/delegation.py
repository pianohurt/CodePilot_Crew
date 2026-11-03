"""把委派与查询接入既有工具授权和配对路径。"""

import json
from typing import Literal

from pydantic import Field

from codepilot.agent.types import StrictData
from codepilot.core.context import ExecutionContext
from codepilot.core.delegation import ChildRun
from codepilot.tools.registry import PreparedAction, ToolError, bounded_text


class SpawnArgs(StrictData):
    role: Literal["planner", "executor", "reviewer"]
    goal: str = Field(min_length=1, max_length=1400)
    facts: str = Field(default="", max_length=1400)
    constraints: str = Field(default="", max_length=1000)
    acceptance: str = Field(min_length=1, max_length=1000)
    mode: Literal["foreground", "background"] = "foreground"
    allowed_tools: list[Literal["read_file", "list_dir", "write_file"]] | None = None
    max_steps: int = Field(default=4, ge=1, le=8)


class ResultArgs(StrictData):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    wait_seconds: float = Field(default=2, ge=0, le=10, allow_inf_nan=False)
    cancel: bool = False


def child_observation(job: ChildRun) -> str:
    result = job.result
    payload = {"run_id": job.run_id, "status": "running"}
    if result is not None:
        payload = {
            "run_id": job.run_id, "status": result.status, "stop_reason": result.stop_reason, "steps": result.steps,
            "answer": bounded_text(result.answer, 768), "error": bounded_text(result.error or "", 256),
        }
    text = json.dumps(payload, ensure_ascii=False)
    if result is not None and result.status != "success":
        raise ToolError(text, stop_run=result.stop_reason == "tool_cleanup_failed")
    return text


def prepare_spawn(args: SpawnArgs, context: ExecutionContext) -> PreparedAction:
    tree = context.tree
    if tree is None or tree.root is not context:
        raise ToolError("delegation_depth_limit")
    if not args.goal.strip() or not args.acceptance.strip():
        raise ToolError("delegation_requires_goal_and_acceptance")

    async def invoke() -> str:
        return child_observation(await tree.spawn(args, context))

    return PreparedAction(args.model_dump(), invoke, None)


def prepare_result(args: ResultArgs, context: ExecutionContext) -> PreparedAction:
    tree = context.tree
    if tree is None or tree.root is not context:
        raise ToolError("unknown_child_run")

    async def invoke() -> str:
        return child_observation(await tree.query(args.run_id, args.wait_seconds, cancel=args.cancel))

    return PreparedAction(args.model_dump(), invoke, None)
