"""一个 stdio 服务对应一个进程和串行 JSON-RPC 通道。"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from codepilot import get_version
from codepilot.mcp.config import ServerConfig
from codepilot.tools.command import command_environment, stop_tree

MAX_FRAME = 1024 * 1024
PROTOCOL = "2025-06-18"
SUPPORTED = (PROTOCOL, "2025-03-26", "2024-11-05")


class McpError(RuntimeError):
    pass


class McpRemoteError(McpError):
    pass


def reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


class StdioClient:
    def __init__(self, name: str, config: ServerConfig) -> None:
        self.name, self.config = name, config
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._seq = 0
        self._cleanup: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self, workspace: Path) -> None:
        environment = command_environment()
        executable = shutil.which(self.config.command, path=environment["PATH"])
        if executable is None or (os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}):
            raise McpError(f"MCP {self.name}: configure a direct executable")
        worker = Path(__file__).resolve().parents[1] / "tools" / "command_worker.py"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys._base_executable, str(worker), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=environment, limit=MAX_FRAME, **options,
            )
            async with asyncio.timeout(self.config.timeout_seconds):
                ready = await self._read()
                if ready != {"supervisor": "ready", "pid": self.process.pid}:
                    raise McpError(f"MCP {self.name}: supervisor unavailable")
                await self._send({"argv": [executable, *self.config.args], "cwd": str(workspace), "stdio": True})
                if await self._read() != {"supervisor": "starting"}:
                    raise McpError(f"MCP {self.name}: supervisor rejected startup")
            info = await self.request("initialize", {
                "protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "CodePilot Crew", "version": get_version()},
            })
            capabilities = info.get("capabilities")
            if info.get("protocolVersion") not in SUPPORTED:
                raise McpError(f"MCP {self.name}: unsupported protocol version")
            if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
                raise McpError(f"MCP {self.name}: server did not advertise tools")
            async with asyncio.timeout(self.config.timeout_seconds):
                await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except (OSError, TimeoutError) as exc:
            raise McpError(f"MCP {self.name}: startup failed or timed out") from exc

    async def _send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        data = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(data) > MAX_FRAME:
            raise McpError(f"MCP {self.name}: outgoing frame exceeds limit")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        try:
            line = await self.process.stdout.readline()
            if not line:
                raise McpError(f"MCP {self.name}: connection closed")
            if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                raise ValueError("Invalid frame")
            message = json.loads(line.decode("utf-8"), parse_constant=reject_constant)
            if not isinstance(message, dict):
                raise ValueError("Expected JSON object")
        except (ValueError, RecursionError) as exc:
            raise McpError(f"MCP {self.name}: invalid or oversized frame") from exc
        return message

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                raise McpError(f"MCP {self.name}: connection unavailable")
            self._seq += 1
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    await self._send({"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params})
                    return await self._receive(self._seq)
            except McpRemoteError:
                raise
            except TimeoutError as exc:
                await self.close()
                raise McpError(f"MCP {self.name}: {method} timed out") from exc
            except (OSError, McpError, asyncio.CancelledError):
                await self.close()
                raise

    async def _receive(self, request_id: int) -> dict[str, Any]:
        for _ in range(65):
            message = await self._read()
            if message.get("jsonrpc") != "2.0":
                break
            method = message.get("method")
            if isinstance(method, str):
                if "id" in message:
                    response = {"jsonrpc": "2.0", "id": message["id"]}
                    if method == "ping":
                        response["result"] = {}
                    else:
                        response["error"] = {"code": -32601, "message": "Method not supported"}
                    await self._send(response)
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                break
            if "error" in message and "result" not in message:
                error = message["error"]
                if isinstance(error, dict) and type(error.get("code")) is int and isinstance(error.get("message"), str):
                    raise McpRemoteError(f"MCP {self.name} RPC {error['code']}: {error['message']}")
                break
            if "error" not in message and isinstance(message.get("result"), dict):
                return message["result"]
            break
        raise McpError(f"MCP {self.name}: invalid response for request {request_id}")

    async def close(self) -> None:
        self._closed = True
        if self.process is None:
            return
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close_process(self.process))
        try:
            await asyncio.shield(self._cleanup)
        except asyncio.CancelledError:
            await self._cleanup
            raise

    @staticmethod
    async def _close_process(process: asyncio.subprocess.Process) -> None:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.close()

        async def drain() -> None:
            while await process.stdout.read(65536):
                pass

        reader = asyncio.create_task(drain())
        try:
            try:
                await asyncio.wait_for(process.wait(), 0.5)
            except TimeoutError:
                pass
            await stop_tree(process)
            await asyncio.wait_for(reader, 5)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
