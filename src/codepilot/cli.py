"""S0 命令入口：参数、用户输出与进程退出码。"""

import argparse
import asyncio
import sys

from pydantic import ValidationError

from codepilot import get_version
from codepilot.core.app import run_core
from codepilot.core.config import Settings, load_settings
from codepilot.core.logging_setup import configure_logging
from codepilot.protocol.messages import PingResult
from codepilot.transport.client import ProtocolError, RemoteError, request


async def ping(settings: Settings) -> None:
    result = await request(settings.host, settings.port, "core.ping", {}, timeout=settings.timeout)
    try:
        pong = PingResult.model_validate(result)
    except ValidationError as exc:
        raise ProtocolError("Invalid core.ping result") from exc
    print(pong.message)


def main() -> int:
    parser = argparse.ArgumentParser(prog="codepilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="显示本地安装版本")
    core = commands.add_parser("core", help="在前台启动 Core，Ctrl+C 退出")
    core.add_argument("--port", type=int)
    core.add_argument("--log-level")
    probe = commands.add_parser("ping", help="探活已有 Core，不自动启动")
    probe.add_argument("--port", type=int)
    probe.add_argument("--timeout", type=float)
    args = parser.parse_args()
    if args.command == "version":
        print(f"codepilot {get_version()}")
        return 0
    try:
        settings = load_settings(
            port=args.port,
            timeout=getattr(args, "timeout", None),
            log_level=getattr(args, "log_level", None),
        )
    except ValidationError as exc:
        details = "; ".join(f"{item['loc'][0]}: {item['msg']}" for item in exc.errors())
        print(f"Invalid configuration: {details}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_core(settings) if args.command == "core" else ping(settings))
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
