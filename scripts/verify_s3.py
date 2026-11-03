"""在临时示例中实际批准受限修改与检查；不批准任意模型命令。"""

import argparse
import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from pydantic import ValidationError
from textual.widgets import Button, Input

from codepilot import get_version
from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings, load_model_settings
from codepilot.core.runner import Runner
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp

FIXED_CONTENT = "def add(a, b):\n    return a + b\n"


def permitted(preview: dict[str, object], workspace: Path) -> bool:
    parameters = preview["parameters"]
    if preview["name"] == "write_file":
        if parameters.get("path") != str(workspace / "calc.py") or parameters.get("overwrite") is not True:
            return False
        try:
            return ast.dump(ast.parse(parameters["content"])) == ast.dump(ast.parse(FIXED_CONTENT))
        except (SyntaxError, ValueError):
            return False
    if preview["name"] == "bash":
        return parameters.get("command") == "python check.py" and parameters.get("cwd") == str(workspace) and parameters.get("timeout_seconds", 121) <= 30
    return False


async def verify(mode: str, env_file: Path, output: Path) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    marker = "CHECK_OK_" + uuid4().hex
    with TemporaryDirectory(prefix="codepilot-s3-") as temporary:
        workspace = Path(temporary).resolve()
        target = workspace / "calc.py"
        before = "def add(a, b):\n    return a - b\n"
        target.write_text(before, encoding="utf-8")
        (workspace / "check.py").write_text(f"import sys\nsys.dont_write_bytecode = True\nfrom calc import add\nassert add(2, 3) == 5\nassert add(-2, 4) == 2\nprint({marker!r})\n", encoding="utf-8")
        baseline = subprocess.run([sys.executable, "check.py"], cwd=workspace, capture_output=True, timeout=5)
        async with httpx.AsyncClient() as http:
            secrets = ()
            model_name = "scripted"
            if mode == "real":
                settings = load_model_settings(env_file)
                secrets = (settings.api_key.get_secret_value(),)
                model_name = settings.model
                http.timeout = httpx.Timeout(settings.model_timeout)
                provider = AnthropicProvider(http, base_url=settings.base_url, api_key=secrets[0], model=settings.model, max_tokens=settings.max_tokens)
            else:
                provider = FixedResponseProvider([
                    ModelResponse(content=[ToolUse(id="read-calc", name="read_file", input={"path": "calc.py"}), ToolUse(id="read-check", name="read_file", input={"path": "check.py"})], stop_reason="tool_use"),
                    ModelResponse(content=[ToolUse(id="check-before", name="bash", input={"command": "python check.py"})], stop_reason="tool_use"),
                    ModelResponse(content=[ToolUse(id="fix", name="write_file", input={"path": "calc.py", "content": FIXED_CONTENT, "overwrite": True})], stop_reason="tool_use"),
                    ModelResponse(content=[ToolUse(id="check-after", name="bash", input={"command": "python check.py"})], stop_reason="tool_use"),
                    ModelResponse(content=[TextBlock(text=f"固定响应：修正加法并通过检查 {marker}")], stop_reason="end_turn"),
                ])
            runner = Runner(provider, builtin_registry(), workspace, record_dir=output.parent / f"s3-{mode}-runs", secrets=secrets)
            server = SocketServer(CoreService(runner).handle, port=0)
            await server.start()
            app = CodePilotApp(Settings(port=server.port))
            decisions: dict[str, dict[str, object]] = {}
            try:
                async with app.run_test(size=(110, 50)) as pilot:
                    async with asyncio.timeout(120):
                        while app.query_one("#start", Button).disabled:
                            await asyncio.sleep(0.02)
                        app.query_one("#goal", Input).value = (
                            "先用相对路径读取 calc.py 和 check.py，定位并修正导致检查失败的错误。仅允许使用 write_file 修改 calc.py，"
                            "保持 def add(a, b) 的单一函数结构，不增加导入/类型提示/文档字符串，overwrite=true。"
                            "必须使用 bash 工具执行唯一允许的命令 python check.py（cwd=.，timeout_seconds不超过30），"
                            "不要修改 check.py 或绕过失败。最终回答引用实际检查输出的 CHECK_OK 完整标记。"
                        )
                        await pilot.click("#start")
                        while app.active_run is None or app.views[app.active_run].result is None:
                            view = app.views.get(app.active_run)
                            if view and view.approvals:
                                approval_id, preview = next(iter(view.approvals.items()))
                                if approval_id not in decisions:
                                    allow = permitted(preview, workspace)
                                    decisions[approval_id] = {"tool": preview["name"], "allow": allow}
                                    await pilot.pause()
                                    await pilot.click("#allow" if allow else "#deny")
                            await asyncio.sleep(0.02)
                    view = app.views[app.active_run]
                    after = subprocess.run([sys.executable, "check.py"], cwd=workspace, capture_output=True, timeout=5)
                    directory = output.parent / f"s3-{mode}-runs" / view.run_id
                    traces = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
                    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                    successful = {item["tool_use_id"] for item in events if item["type"] == "tool.completed" and not item["data"]["is_error"]}
                    read_targets = {(workspace / item["data"]["input"]["path"]).resolve().name for item in events if item["type"] == "tool.started" and item["data"]["name"] == "read_file" and item["tool_use_id"] in successful}
                    read_ok = {"calc.py", "check.py"}.issubset(read_targets)
                    approved_order = True
                    for item in events:
                        if item["type"] == "tool.started" and item["data"]["name"] in {"write_file", "bash"}:
                            approved_order &= any(approval["type"] == "approval.resolved" and approval["tool_use_id"] == item["tool_use_id"] and approval["data"]["status"] == "allowed" and approval["seq"] < item["seq"] for approval in events)
                    observed = any(
                        block.get("type") == "tool_result" and not block.get("is_error") and block.get("content", "").startswith("exit_code=0") and marker in block["content"]
                        for item in traces if item["kind"] == "model.request"
                        for message in item["data"].get("messages", []) for block in message["content"]
                    )
                    write_ok = any(call.name == "write_file" and not call.is_error for call in view.result.tool_calls)
                    command_ok = any(call.name == "bash" and not call.is_error for call in view.result.tool_calls)
                    passed = baseline.returncode != 0 and after.returncode == 0 and read_ok and approved_order and write_ok and command_ok and observed and view.status == "success" and marker in view.answer
                    await pilot.pause()
                    output.with_suffix(".svg").write_text(app.export_screenshot(title=f"CodePilot Crew S3 · {mode}"), encoding="utf-8")
                    report = {"version": get_version(), "provider": mode, "model": model_name, "passed": passed, "baseline_exit": baseline.returncode, "final_check_exit": after.returncode,
                              "read_sources": read_ok, "authorized_before_start": approved_order, "write_succeeded": write_ok, "command_succeeded": command_ok, "observed_check_output": observed, "approvals": list(decisions.values()),
                              "result": view.result.model_dump(), "record_directory": str(directory.resolve()), "before": before, "after": target.read_text(encoding="utf-8")}
                    serialized = json.dumps(report, ensure_ascii=False, indent=2)
                    for secret in secrets:
                        serialized = serialized.replace(secret, "[REDACTED]")
                    output.write_text(serialized + "\n", encoding="utf-8")
                    print(json.dumps({key: value for key, value in report.items() if key not in {"result", "record_directory", "before", "after"}}, ensure_ascii=False))
                    return passed
            finally:
                await server.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["fixed", "real"], default="fixed")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        return 0 if asyncio.run(verify(args.provider, args.env_file, args.output or Path("artifacts") / f"s3-{args.provider}.json")) else 1
    except (ValidationError, ValueError):
        print("Verification input or configuration invalid; no secret values were printed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
