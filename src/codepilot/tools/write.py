"""小文件原子替换；预检无写入，批准后检查内容快照，冲突不重放。"""

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from codepilot.core.context import ExecutionContext
from codepilot.tools.readonly import resolve_path
from codepilot.tools.registry import PreparedAction, ToolError


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=2048)
    content: str = Field(max_length=10000)
    overwrite: bool = False


def snapshot(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ToolError("not_a_regular_file")
    with path.open("rb") as stream:
        content = stream.read(128 * 1024 + 1)
    if len(content) > 128 * 1024:
        raise ToolError("existing_file_too_large")
    return hashlib.sha256(content).hexdigest()


def prepare_write(args: WriteFileArgs, context: ExecutionContext) -> PreparedAction:
    data = args.content.encode("utf-8")
    if len(data) > 12 * 1024:
        raise ToolError("write_content_too_large")
    target = resolve_path(context.workspace, args.path)
    if not target.parent.is_dir():
        raise ToolError("parent_directory_missing")
    previous = snapshot(target)
    if previous is not None and not args.overwrite:
        raise ToolError("file_exists_overwrite_required")

    async def commit() -> str:
        if resolve_path(context.workspace, args.path) != target or snapshot(target) != previous:
            raise ToolError("write_conflict: target changed since approval")
        descriptor, temporary = tempfile.mkstemp(prefix=".codepilot-write-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if previous is not None:
                os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            if snapshot(target) != previous:
                raise ToolError("write_conflict: target changed during preparation")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.chmod(temporary, stat.S_IWRITE | stat.S_IREAD)
                os.unlink(temporary)
        return f"written={target.relative_to(context.workspace).as_posix()}\nbytes={len(data)}\nsha256={hashlib.sha256(data).hexdigest()}"

    return PreparedAction({"path": str(target), "requested_path": args.path, "content": args.content, "overwrite": args.overwrite, "previous_sha256": previous}, commit, 5.0)
