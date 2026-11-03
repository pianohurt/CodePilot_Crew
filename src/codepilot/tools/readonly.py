"""有界、只读的本地文件访问；路径检查不等于操作系统隔离。"""

from __future__ import annotations

import asyncio
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codepilot.core.context import ExecutionContext
from codepilot.tools.registry import ToolError, ToolRegistry

MAX_FILE_BYTES = 128 * 1024
MAX_DIRECTORY_ENTRIES = 100
BLOCKED_PARTS = frozenset({".git", ".venv", ".cache", ".codepilot"})


def restricted(parts: tuple[str, ...]) -> bool:
    return any(part.casefold() in BLOCKED_PARTS or part.casefold().startswith(".env") for part in parts)


def resolve_path(workspace: Path, value: str) -> Path:
    supplied = Path(value)
    target = (workspace / supplied).resolve()
    if not target.is_relative_to(workspace):
        raise ToolError("path_outside_workspace")
    relative_parts = target.relative_to(workspace).parts
    if restricted(supplied.parts) or restricted(relative_parts) or any(":" in part for part in relative_parts):
        raise ToolError("restricted_path")
    return target


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    path: str = Field(min_length=1, max_length=2048)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_line_order(self) -> ReadFileArgs:
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        return self


class ListDirArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    path: str = Field(default=".", min_length=1, max_length=2048)


def _read_file(args: ReadFileArgs, workspace: Path) -> str:
    path = resolve_path(workspace, args.path)
    if not path.is_file():
        if not path.exists():
            raise FileNotFoundError
        raise ToolError("not_a_regular_file")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ToolError("file_too_large")
    lines = data.decode("utf-8-sig").splitlines()
    end = args.end_line if args.end_line is not None else len(lines)
    selected = lines[args.start_line - 1:end]
    return "\n".join(f"{number}: {line}" for number, line in enumerate(selected, args.start_line)) or "(no lines)"


def _list_dir(args: ListDirArgs, workspace: Path) -> str:
    path = resolve_path(workspace, args.path)
    if not path.is_dir():
        if not path.exists():
            raise FileNotFoundError
        raise ToolError("not_a_directory")
    visible = (item for item in path.iterdir() if not restricted((item.name,)))
    entries = list(islice(visible, MAX_DIRECTORY_ENTRIES + 1))
    truncated = len(entries) > MAX_DIRECTORY_ENTRIES
    lines = [item.name + ("/" if item.is_dir() else "") for item in sorted(entries[:MAX_DIRECTORY_ENTRIES], key=lambda item: item.name.casefold())]
    return "\n".join(lines + (["[truncated]"] if truncated else [])) or "(empty directory)"


async def read_file(args: ReadFileArgs, context: ExecutionContext) -> str:
    return await asyncio.to_thread(_read_file, args, context.workspace)


async def list_dir(args: ListDirArgs, context: ExecutionContext) -> str:
    return await asyncio.to_thread(_list_dir, args, context.workspace)


def readonly_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("read_file", "Read UTF-8 text within the workspace, optionally selecting line numbers. Use actual file contents as evidence.", ReadFileArgs, read_file)
    registry.register("list_dir", "List visible names in a workspace directory. Does not recursively read files or expose restricted paths.", ListDirArgs, list_dir)
    return registry
