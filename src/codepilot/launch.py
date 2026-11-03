"""探活、首次配置与本次命令拥有的 Core 生命周期。"""

import asyncio
from contextlib import asynccontextmanager
from getpass import GetPassWarning, getpass
import json
import os
from pathlib import Path
import sys
from typing import AsyncIterator
import warnings

from pydantic import ValidationError

from codepilot import get_version
from codepilot.core.app import run_core
from codepilot.core.config import ModelSettings, Settings, load_model_settings
from codepilot.core.permissions import load_policy
from codepilot.mcp.config import load_mcp_config
from codepilot.transport.rpc import RemoteError, RpcClient


def setup_model(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"配置已存在，请编辑 {path}")
    if not sys.stdin.isatty():
        raise RuntimeError("配置向导需要交互终端。请提供 --env-file 或设置 CODEPILOT_CREW 模型环境变量")
    base_url = input("Anthropic 兼容 Base URL [https://api.anthropic.com]: ").strip() or "https://api.anthropic.com"
    model = input("模型名称: ").strip()
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            key = getpass("API Key（隐藏输入）: ").strip()
        except GetPassWarning as exc:
            raise RuntimeError("当前终端无法隐藏密钥输入") from exc
    try:
        settings = ModelSettings(base_url=base_url, model=model, api_key=key)
    except ValidationError as exc:
        raise RuntimeError("请检查 Base URL、模型名称和 API Key") from exc
    fields = {"BASE_URL": settings.base_url, "MODEL": settings.model, "API_KEY": settings.api_key.get_secret_value()}
    contents = "".join(f"CODEPILOT_CREW_{name}={json.dumps(value, ensure_ascii=False)}\n" for name, value in fields.items())
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        stream.write(contents)
    print(f"已保存模型配置 {path}")


def start_model(workspace: Path, env_file: Path | None) -> ModelSettings:
    path = env_file if env_file is not None else workspace / ".env"
    try:
        model = load_model_settings(path if path.exists() or env_file is not None else None)
    except (ValidationError, ValueError) as exc:
        raise RuntimeError(f"模型配置无效，请检查 {path} 或 CODEPILOT_CREW 模型环境变量") from exc
    if model is not None:
        return model
    if not sys.stdin.isatty():
        raise RuntimeError(f"缺少模型配置，请运行 codepilot setup 或提供 --env-file（默认 {path}）")
    setup_model(path)
    return load_model_settings(path)


@asynccontextmanager
async def available_core(
    settings: Settings, workspace: Path, *, env_file: Path | None = None, policy_file: Path | None = None,
    mcp_file: Path | None = None,
) -> AsyncIterator[None]:
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise RuntimeError("工作区必须是目录")
    probe = RpcClient(settings.host, settings.port, timeout=min(settings.timeout, 2))
    info = None
    try:
        try:
            await probe.connect()
        except (ConnectionRefusedError, TimeoutError):
            # Windows 上空端口也可能先等到连接超时，最终由 Core 的 bind 仲裁。
            pass
        else:
            try:
                info = await probe.call("core.info", {})
            except RemoteError as exc:
                raise RuntimeError("该端口的服务不支持 Core 自动复用，请检查端口") from exc
    finally:
        await probe.close()
    if info is not None:
        if info.get("version") != get_version() or info.get("workspace") != str(workspace) or info.get("model_ready") is not True:
            raise RuntimeError("该端口的 Core 版本、工作区或模型配置不匹配，请关闭现有 Core 或指定另一端口")
        if env_file is not None or policy_file is not None or mcp_file is not None:
            raise RuntimeError("现有 Core 已加载自己的配置。应用新的启动配置请使用另一端口")
        yield
        return

    model = start_model(workspace, env_file)
    try:
        policy = load_policy(policy_file)
        mcp_config = load_mcp_config(mcp_file)
    except (ValidationError, ValueError) as exc:
        raise RuntimeError("权限策略或 MCP 配置无效，请检查所选 JSON 文件") from exc
    ready = asyncio.Event()
    core = asyncio.create_task(run_core(settings, workspace=workspace, model=model, policy=policy, mcp_config=mcp_config, ready=ready))
    startup = asyncio.create_task(ready.wait())
    try:
        await asyncio.wait({startup, core}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        if core.done():
            core.result()
            raise RuntimeError("Core 在启动时退出")
        if not ready.is_set():
            raise RuntimeError("Core 启动超时，请检查 MCP 服务和端口")
        yield
    finally:
        startup.cancel()
        core.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        try:
            await core
        except asyncio.CancelledError:
            pass
