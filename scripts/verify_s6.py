"""真实模型通过前台 planner 与 reviewer 核对一个临时模块。"""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from codepilot.agent.provider import AnthropicProvider
from codepilot.core.config import load_model_settings
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.tools.builtin import builtin_registry


async def verify(env_file: Path) -> bool:
    settings = load_model_settings(env_file)
    key = settings.api_key.get_secret_value()
    marker = "MODULE_" + uuid4().hex[:12]
    with TemporaryDirectory(prefix="codepilot-s6-") as directory:
        workspace = Path(directory).resolve()
        (workspace / "sample.py").write_text(f'VERIFICATION_MARKER = "{marker}"\n', encoding="utf-8")
        events = []
        async with httpx.AsyncClient(timeout=settings.model_timeout) as client:
            provider = AnthropicProvider(
                client, base_url=settings.base_url, api_key=key, model=settings.model,
                max_tokens=settings.max_tokens, context_budget=settings.context_budget,
            )
            runner = Runner(
                provider, builtin_registry(), workspace, record_dir=workspace / "runs",
                policy=PermissionPolicy(write_file="deny", bash="deny", note_save="deny"), secrets=(key,),
            )
            result = await runner.run(
                "Use spawn_agent twice, sequentially in foreground. First delegate to planner to read sample.py "
                "and return the exact VERIFICATION_MARKER from the file. Then delegate to reviewer to independently "
                "read sample.py and compare with the planner's finding, supplying the finding in facts. Both children "
                "must use read_file and make no changes. Pass the planner's exact marker to the reviewer in facts. "
                "Report the exact verified marker and whether planner and reviewer agreed.",
                stream=True, on_event=events.append,
            )
        children = [event.data for event in events if event.type == "agent.finished"]
        reads = []
        reviewer_received_marker = False
        for child in children:
            file = workspace / "runs" / child["child_run_id"] / "events.jsonl"
            child_events = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
            if child["role"] == "reviewer":
                goal = child_events[0]["data"]["goal"]
                reviewer_received_marker = marker in goal.partition("Input facts: ")[2].partition("\nConstraints: ")[0]
            reads.append(any(
                event["type"] == "tool.completed" and event["data"]["name"] == "read_file"
                and not event["data"]["is_error"] and marker in event["data"]["content"]
                for event in child_events
            ))
        passed = (
            result.status == "success" and marker in result.answer and
            [child["role"] for child in children] == ["planner", "reviewer"] and all(reads)
            and all(child["status"] == "success" for child in children)
            and reviewer_received_marker
        )
        print(json.dumps({
            "model": settings.model, "passed": passed, "root_status": result.status,
            "root_stop_reason": result.stop_reason, "root_steps": result.steps,
            "root_tools": [receipt.name for receipt in result.tool_calls],
            "children": [{"role": child["role"], "status": child["status"], "actual_read": read}
                         for child, read in zip(children, reads, strict=True)],
            "marker_verified": marker in result.answer, "reviewer_received_marker": reviewer_received_marker,
            "tree_usage": result.usage,
        }))
        return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    raise SystemExit(0 if asyncio.run(verify(parser.parse_args().env_file)) else 1)
