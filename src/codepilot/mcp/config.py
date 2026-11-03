"""MCP 进程由 Core 的本地配置启动。"""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

type ServerName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,24}$")]


class ServerConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    command: str = Field(min_length=1, max_length=1024)
    args: list[str] = Field(default_factory=list, max_length=64)
    timeout_seconds: FiniteFloat = Field(default=10.0, gt=0, le=60)
    tools: list[str] | None = Field(default=None, max_length=128)


class McpConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    servers: dict[ServerName, ServerConfig] = Field(default_factory=dict, max_length=8)


def load_mcp_config(path: Path | None) -> McpConfig:
    return McpConfig() if path is None else McpConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
