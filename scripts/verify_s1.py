"""分别记录固定响应链路与真实模型读取证据，不混用两类结果。"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider, Provider
from codepilot.agent.types import Message, ModelResponse, RunResult, TextBlock, ToolDefinition, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import load_model_settings
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.server import SocketServer


def evidence_view(value: object) -> object:
    if isinstance(value, dict):
        if value.get("type") == "thinking":
            return {"type": "thinking", "thinking": "[opaque omitted]", "signature": "[opaque omitted]"}
        return {key: evidence_view(item) for key, item in value.items()}
    if isinstance(value, list):
        return [evidence_view(item) for item in value]
    return value


class RecordingProvider:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self.steps: list[dict[str, object]] = []

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        record: dict[str, object] = {
            "messages": [evidence_view(item.model_dump()) for item in messages],
            "tools": [item.model_dump() for item in tools],
        }
        self.steps.append(record)
        response = await self._provider.complete(messages, tools)
        record["response"] = evidence_view(response.model_dump())
        return response


async def run_via_cli(runner: Runner, goal: str) -> RunResult:
    server = SocketServer(CoreService(runner).handle, port=0)
    await server.start()
    process = None
    try:
        env = {**os.environ, "PYTHONUTF8": "1"}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "codepilot.cli", "run", goal, "--port", str(server.port),
            "--max-steps", "4", "--timeout", "120", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 125)
        if not stdout:
            raise RuntimeError("CLI did not return a structured run result")
        result = RunResult.model_validate_json(stdout)
        expected = 0 if result.status == "success" else 1
        if process.returncode != expected or stderr:
            raise RuntimeError("CLI exit code or diagnostics did not match run result")
        return result
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await server.close()


async def verify(provider_name: str, env_file: Path, output: Path, via_rpc: bool) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    secret = ""
    metadata: dict[str, object] = {"provider": provider_name, "via_rpc_and_cli": via_rpc}
    marker = uuid4().hex
    with TemporaryDirectory(prefix="codepilot-s1-") as directory:
        workspace = Path(directory)
        (workspace / "sample.txt").write_text(
            f"verification_marker={marker}\npurpose=CodePilot Crew read-only S1 verification\n", encoding="utf-8"
        )
        goal = (
            "必须调用 read_file 读取工作目录中的 sample.txt（使用相对路径），"
            "最终回答给出文件中的 verification_marker 完整值和 purpose。"
            "这些内容未在问题中提供，请基于实际读取结果回答，不要猜测。"
        )
        async with httpx.AsyncClient() as client:
            if provider_name == "real":
                settings = load_model_settings(env_file)
                if settings is None:
                    raise ValueError("Model configuration is missing")
                secret = settings.api_key.get_secret_value()
                client.timeout = httpx.Timeout(settings.model_timeout)
                provider: Provider = AnthropicProvider(
                    client, base_url=settings.base_url, api_key=secret, model=settings.model, max_tokens=settings.max_tokens
                )
                metadata.update({"model": settings.model, "endpoint_host": urlsplit(settings.base_url).hostname})
            else:
                provider = FixedResponseProvider([
                    ModelResponse(content=[ToolUse(id="fixed-read", name="read_file", input={"path": "sample.txt"})], stop_reason="tool_use"),
                    ModelResponse(content=[TextBlock(text=f"固定响应：{marker}; CodePilot Crew read-only S1 verification")], stop_reason="end_turn"),
                ])
            recorder = RecordingProvider(provider)
            runner = Runner(recorder, readonly_registry(), workspace)
            result = await run_via_cli(runner, goal) if via_rpc else await runner.run(goal, max_steps=4)
        observed = any(
            marker in json.dumps(step["messages"], ensure_ascii=False) for step in recorder.steps[1:]
        )
        actual_read = any(item.name == "read_file" and not item.is_error for item in result.tool_calls)
        passed = result.status == "success" and actual_read and observed and marker in result.answer
        evidence = {**metadata, "passed": passed, "actual_read": actual_read, "observation_in_next_request": observed,
                    "marker": marker, "result": result.model_dump(), "steps": recorder.steps}
        serialized = json.dumps(evidence, ensure_ascii=False, indent=2)
        if secret:
            serialized = serialized.replace(secret, "[REDACTED]")
        output.write_text(serialized + "\n", encoding="utf-8")
        print(json.dumps({**metadata, "passed": passed, "status": result.status, "stop_reason": result.stop_reason,
                          "steps": result.steps, "actual_read": actual_read, "observation_in_next_request": observed,
                          "error": result.error, "evidence": str(output.resolve())}, ensure_ascii=False))
        return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["fixed", "real"], default="fixed")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--via-rpc", action="store_true")
    args = parser.parse_args()
    output = args.output or Path("artifacts") / f"s1-{args.provider}.json"
    try:
        return 0 if asyncio.run(verify(args.provider, args.env_file, output, args.via_rpc)) else 1
    except (ValidationError, ValueError):
        print("Model configuration is missing or invalid; no secret values were printed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
