"""策略来自服务端；模型文字与客户端运行参数都不能放宽它。"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from codepilot.agent.types import ToolUse

if TYPE_CHECKING:
    from codepilot.core.context import ExecutionContext

type Decision = Literal["allow", "deny", "ask"]
type ApprovalCallback = Callable[[ToolUse, dict[str, Any], "ExecutionContext"], Awaitable[bool]]


class PermissionPolicy(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    read_file: Decision = "allow"
    list_dir: Decision = "allow"
    write_file: Decision = "ask"
    bash: Decision = "ask"
    note_save: Decision = "ask"

    def decision(self, name: str) -> Decision:
        return getattr(self, name, "deny")


class ApprovalError(ValueError):
    pass


def load_policy(path: Path | None) -> PermissionPolicy:
    if path is None:
        return PermissionPolicy()
    return PermissionPolicy.model_validate_json(path.read_text(encoding="utf-8-sig"))
