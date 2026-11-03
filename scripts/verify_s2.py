"""挂载真实 Textual 界面，分别验证固定流和真实模型的只读任务。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from pydantic import ValidationError
from textual.widgets import Button, Input, Static

from codepilot import get_version
from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings, load_model_settings
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


async def verify(mode: str, env_file: Path, output: Path) -> bool:
    marker = uuid4().hex
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="codepilot-s2-") as temporary:
        workspace = Path(temporary)
        (workspace / "sample.txt").write_text(f"verification_marker={marker}\npurpose=CodePilot Crew S2\n", encoding="utf-8")
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
                    ModelResponse(content=[ToolUse(id="read-a", name="read_file", input={"path": "sample.txt"})], stop_reason="tool_use"),
                    ModelResponse(content=[TextBlock(text=f"固定流回答：{marker} / CodePilot Crew S2")], stop_reason="end_turn"),
                ])
            runner = Runner(provider, readonly_registry(), workspace, record_dir=output.parent / f"s2-{mode}-runs", secrets=secrets)
            server = SocketServer(CoreService(runner).handle, port=0)
            await server.start()
            app = CodePilotApp(Settings(port=server.port))
            try:
                async with app.run_test(size=(110, 40)) as pilot:
                    async with asyncio.timeout(120):
                        while app.query_one("#start", Button).disabled:
                            await asyncio.sleep(0.02)
                        app.query_one("#goal", Input).value = "必须使用 read_file 读取 sample.txt，相对路径，最终回答文件中的 verification_marker 完整值和 purpose。不要猜测。"
                        await pilot.click("#start")
                        while app.active_run is None or app.views[app.active_run].result is None:
                            await asyncio.sleep(0.02)
                    view = app.views[app.active_run]
                    directory = output.parent / f"s2-{mode}-runs" / view.run_id
                    trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
                    observed = any(item["kind"] == "model.request" and item["step"] >= 2 and marker in json.dumps(item["data"], ensure_ascii=False) for item in trace)
                    actual_read = any(item.name == "read_file" and not item.is_error for item in view.result.tool_calls)
                    displayed = str(app.query_one("#answer", Static).content)
                    passed = view.status == "success" and actual_read and observed and marker in view.answer and displayed == view.answer and view.delta_count > 0
                    await pilot.pause()
                    output.with_suffix(".svg").write_text(app.export_screenshot(title=f"CodePilot Crew S2 · {mode}"), encoding="utf-8")
                    summary = {"version": get_version(), "provider": mode, "model": model_name, "passed": passed,
                               "mounted_tui": True, "delta_events": view.delta_count, "actual_read": actual_read,
                               "observation_in_next_request": observed, "final_widget_matches_answer": displayed == view.answer,
                               "result": view.result.model_dump(), "record_directory": str(directory.resolve())}
                    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    print(json.dumps({key: value for key, value in summary.items() if key not in {"result", "record_directory"}}, ensure_ascii=False))
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
        return 0 if asyncio.run(verify(args.provider, args.env_file, args.output or Path("artifacts") / f"s2-{args.provider}.json")) else 1
    except (ValidationError, ValueError):
        print("Verification input or model configuration is invalid; no secret values were printed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
