"""固定任务验收与可选真实 API 重复评测，输出聚合指标。"""

import argparse
from collections import Counter
from collections.abc import Iterator
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from unittest.mock import patch

from codepilot import get_version
from codepilot.agent.types import ToolResult, ToolUse
from codepilot.core.config import load_model_settings
from codepilot.core.context import ExecutionContext
from codepilot.core.events import EventBus, RunEvent
from codepilot.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "read_location": "test_s1_rpc.py::test_cli_rpc_fixed_response_real_file_round_trip",
    "approved_edit": "test_s3_approvals.py::test_approval_lifecycle_and_connection_ownership[allow]",
    "permission_denial": "test_s3_approvals.py::test_approval_lifecycle_and_connection_ownership[deny]",
    "stream_disconnect": "test_s5_stream_retry.py::test_retry_never_executes_partial_tool_calls_or_replays_writes",
    "long_history": "test_s5_context.py::test_restored_history_is_compacted_without_mutating_originals[continue]",
    "child_failure": "test_s6_delegation.py::test_background_result_reflects_actual_state_while_parent_works[failed]",
    "mcp_error": "test_s7_mcp.py::test_failed_write_is_never_replayed_and_broken_connections_are_quarantined[error]",
    "skill_scope": "test_s8_skills.py::test_missing_and_empty_whitelists_are_distinct_at_execution[allowed1]",
}


def model_calls(records: list[tuple[str, int, str]]) -> int:
    kinds = Counter(records)
    steps = {(run, step) for run, step, _ in kinds}
    return sum(
        (kinds[run, step, "model.attempt.started"] or kinds[run, step, "model.request"])
        + kinds[run, step, "context.summary.request"]
        for run, step in steps
    )


def repeats(calls: Counter[tuple[str, str, str]]) -> int:
    return sum(count - 1 for count in calls.values())


def offline() -> list[dict[str, Any]]:
    import pytest

    class Metrics:
        def __init__(self) -> None:
            self.rows: dict[str, dict[str, Any]] = {}

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
            row = self.rows.setdefault(item.nodeid, {"passed": False})
            calls: Counter[tuple[str, str, str]] = Counter()
            records: list[tuple[str, int, str]] = []
            usage: Counter[str] = Counter()
            approvals: Counter[str] = Counter()
            children: set[str] = set()
            publish, execute, record = EventBus.publish, ToolRegistry.execute, ExecutionContext.record
            started = perf_counter()

            def event(bus: EventBus, value: RunEvent) -> None:
                if value.type == "run.started" and value.data.get("parent_run_id"):
                    children.add(value.run_id)
                if value.type == "run.finished" and value.run_id not in children:
                    usage.update(value.data["result"].get("usage", {}))
                if value.type == "approval.resolved":
                    approvals[value.data["status"]] += 1
                publish(bus, value)

            async def dispatch(registry: ToolRegistry, call: ToolUse, context: ExecutionContext) -> ToolResult:
                calls[context.run_id, call.name, json.dumps(call.input, sort_keys=True)] += 1
                return await execute(registry, call, context)

            def journal(context: ExecutionContext, kind: str, **data: Any) -> None:
                if kind in {"model.request", "context.summary.request", "model.attempt.started"}:
                    records.append((context.run_id, context.step, kind))
                record(context, kind, **data)

            with patch.object(EventBus, "publish", event), patch.object(ToolRegistry, "execute", dispatch), patch.object(ExecutionContext, "record", journal):
                yield
            row.update(
                elapsed_seconds=round(perf_counter() - started, 3), model_requests=model_calls(records),
                tool_dispatches=calls.total(), repeated_tool_dispatches=repeats(calls), usage=dict(usage) or None,
                approval_decisions=dict(approvals), human_interventions=0,
            )

        def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
            if report.when == "call" or report.failed:
                self.rows[report.nodeid]["passed"] = report.passed

    plugin = Metrics()
    selectors = [f"tests/integration/{case}" for case in CASES.values()]
    with TemporaryDirectory(prefix="codepilot-eval-") as directory:
        code = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(Path(directory) / "pytest"), *selectors], plugins=[plugin])
        rows = [{"case": name, **plugin.rows.get(selector, {"passed": False})} for name, selector in zip(CASES, selectors, strict=True)]
        if code and all(row["passed"] for row in rows):
            raise RuntimeError(f"pytest exited with {code}")
        output = Path(directory) / "restart.json"
        started = perf_counter()
        completed = subprocess.run([sys.executable, str(ROOT / "scripts/verify_s4.py"), "--provider", "fixed", "--output", str(output)],
                                   encoding="utf-8", capture_output=True, timeout=120)
        raw = json.loads(output.with_suffix(".records.json").read_text(encoding="utf-8")) if output.exists() else {}
        calls = Counter((run, item["data"]["call"]["name"], json.dumps(item["data"]["call"]["input"], sort_keys=True))
                        for run, entries in raw.items() for item in entries if item["kind"] == "tool.intent")
        records = [(run, item["data"]["step"], item["kind"]) for run, entries in raw.items()
                   for item in entries if item["kind"] in {"model.request", "model.attempt.started", "context.summary.request"}]
        rows.append({
            "case": "session_restart", "passed": completed.returncode == 0,
            "elapsed_seconds": round(perf_counter() - started, 3), "model_requests": model_calls(records),
            "tool_dispatches": calls.total(), "repeated_tool_dispatches": repeats(calls), "usage": None,
            "approval_decisions": {}, "human_interventions": 0,
        })
    return rows


def real(env_file: Path, python: Path, trials: int) -> dict[str, Any]:
    model = load_model_settings(env_file).model
    marker = "S9_LOCATION_7D62"
    source = f'DEFECT_MARKER = "{marker}"\n'
    goal = "Use read_file to read target.py. Report the exact DEFECT_MARKER and file path. Do not modify files."
    rows = []
    with TemporaryDirectory(prefix="codepilot-real-") as directory:
        workspace = Path(directory)
        sample = workspace / "target.py"
        for index in range(1, trials + 1):
            sample.write_text(source, encoding="utf-8")
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = str(reserved.getsockname()[1])
            started = perf_counter()
            try:
                completed = subprocess.run(
                    [str(python), "-m", "codepilot.cli", "start", goal, "--workspace", str(workspace),
                     "--env-file", str(env_file), "--port", port, "--json"],
                    encoding="utf-8", capture_output=True, timeout=150,
                )
            except subprocess.TimeoutExpired:
                rows.append({
                    "trial": index, "passed": False, "elapsed_seconds": round(perf_counter() - started, 3),
                    "model_requests": None, "tool_dispatches": None, "repeated_tool_dispatches": None, "usage": None,
                    "human_interventions": 0, "stop_reason": "launcher_timeout", "error": "CLI exceeded 150 seconds",
                    "exit_code": None,
                })
                continue
            try:
                result = json.loads(completed.stdout)
            except ValueError:
                result = {}
            run_directory = workspace / "artifacts" / "runs" / result.get("run_id", "")
            path = run_directory / "events.jsonl"
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
            trace_path = run_directory / "trace.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()] if trace_path.is_file() else []
            records = [(row["run_id"], row["step"], row["kind"]) for row in trace]
            calls = Counter((event["run_id"], event["data"]["name"], json.dumps(event["data"]["input"], sort_keys=True))
                            for event in events if event["type"] == "tool.started")
            read = any(event["type"] == "tool.completed" and event["data"]["name"] == "read_file"
                       and not event["data"]["is_error"] and marker in event["data"]["content"] for event in events)
            answer = result.get("answer", "")
            rows.append({
                "trial": index, "passed": completed.returncode == 0 and read and marker in answer and "target.py" in answer
                and sample.read_text(encoding="utf-8") == source,
                "elapsed_seconds": round(perf_counter() - started, 3), "model_requests": model_calls(records),
                "tool_dispatches": len(result.get("tool_calls", [])), "repeated_tool_dispatches": repeats(calls),
                "usage": result.get("usage"), "human_interventions": 0,
                "stop_reason": result.get("stop_reason"), "exit_code": completed.returncode,
                "error": result.get("error") if result else "CLI returned no JSON result",
            })
    return {"model": model, "initial_file": source, "goal": goal, "cases": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, help="启用真实模型重复评测")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--trials", type=int, choices=range(2, 6), default=3)
    args = parser.parse_args()
    report = real(args.env_file.resolve(), args.python.resolve(), args.trials) if args.env_file else {"cases": offline()}
    report.update(version=get_version(), mode="real" if args.env_file else "offline")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['mode']} {sum(row['passed'] for row in report['cases'])}/{len(report['cases'])}")
    return 0 if all(row["passed"] for row in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
