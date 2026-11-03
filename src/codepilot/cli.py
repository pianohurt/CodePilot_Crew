"""S0 命令入口：参数、用户输出与进程退出码。"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from codepilot import get_version
from codepilot.core.app import run_core
from codepilot.agent.types import RunResult
from codepilot.core.config import ModelSettings, Settings, load_model_settings, load_settings
from codepilot.core.logging_setup import configure_logging
from codepilot.core.permissions import PermissionPolicy, load_policy
from codepilot.protocol.messages import PingResult
from codepilot.transport.client import ProtocolError, RemoteError, request


async def ping(settings: Settings) -> None:
    result = await request(settings.host, settings.port, "core.ping", {}, timeout=settings.timeout)
    try:
        pong = PingResult.model_validate(result)
    except ValidationError as exc:
        raise ProtocolError("Invalid core.ping result") from exc
    print(pong.message)


async def run_agent(settings: Settings, goal: str, max_steps: int, as_json: bool) -> int:
    raw = await request(settings.host, settings.port, "agent.run", {"goal": goal, "max_steps": max_steps}, timeout=settings.timeout)
    try:
        result = RunResult.model_validate(raw)
    except ValidationError as exc:
        raise ProtocolError("Invalid agent.run result") from exc
    if as_json:
        print(json.dumps(result.model_dump(), ensure_ascii=False))
    elif result.status == "success":
        print(result.answer)
    else:
        print(f"Run failed [{result.stop_reason}]: {result.error}", file=sys.stderr)
    return 0 if result.status == "success" else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="codepilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="显示本地安装版本")
    core = commands.add_parser("core", help="在前台启动 Core，Ctrl+C 退出")
    core.add_argument("--port", type=int)
    core.add_argument("--log-level")
    core.add_argument("--workspace", type=Path, default=Path.cwd())
    core.add_argument("--env-file", type=Path)
    core.add_argument("--record-dir", type=Path)
    core.add_argument("--policy-file", type=Path)
    probe = commands.add_parser("ping", help="探活已有 Core，不自动启动")
    probe.add_argument("--port", type=int)
    probe.add_argument("--timeout", type=float)
    run = commands.add_parser("run", help="运行一次只读 Agent 任务")
    run.add_argument("goal")
    run.add_argument("--port", type=int)
    run.add_argument("--timeout", type=float)
    run.add_argument("--max-steps", type=int, default=8)
    run.add_argument("--json", action="store_true")
    tui = commands.add_parser("tui", help="连接已有 Core 并打开终端工作台")
    tui.add_argument("--port", type=int)
    tui.add_argument("--timeout", type=float)
    args = parser.parse_args()
    if args.command == "version":
        print(f"codepilot {get_version()}")
        return 0
    try:
        timeout = getattr(args, "timeout", None)
        if args.command == "run" and timeout is None:
            timeout = float(os.environ.get("CODEPILOT_CREW_TIMEOUT", "120"))
        settings = load_settings(
            port=args.port,
            timeout=timeout,
            log_level=getattr(args, "log_level", None),
        )
        model: ModelSettings | None = None
        policy = PermissionPolicy()
        if args.command == "core":
            if not args.workspace.is_dir():
                raise ValueError("Workspace must be an existing directory")
            model = load_model_settings(args.env_file)
            policy = load_policy(args.policy_file)
        elif args.command == "run" and (not args.goal.strip() or len(args.goal) > 8000 or not 1 <= args.max_steps <= 32):
            raise ValueError("Goal must contain 1..8000 characters and max_steps must be in 1..32")
    except (ValidationError, ValueError, OSError) as exc:
        details = "; ".join(f"{'.'.join(map(str, item['loc'])) or 'configuration'}: {item['msg']}" for item in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
        print(f"Invalid configuration: {details}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        if args.command == "tui":
            from codepilot.tui.app import CodePilotApp
            CodePilotApp(settings).run()
            return 0
        if args.command == "run":
            return asyncio.run(run_agent(settings, args.goal, args.max_steps, args.json))
        asyncio.run(run_core(settings, workspace=args.workspace, model=model, record_dir=args.record_dir, policy=policy) if args.command == "core" else ping(settings))
        return 0
    except KeyboardInterrupt:
        return 0 if args.command == "core" else 1
    except TimeoutError:
        print("Request or cleanup timed out", file=sys.stderr)
        return 1
    except (OSError, ProtocolError, RemoteError, RuntimeError) as exc:
        print(f"{args.command} failed at {settings.host}:{settings.port}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
