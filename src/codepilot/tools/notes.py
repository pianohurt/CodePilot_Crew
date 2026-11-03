"""显式写入会话笔记；沿用工具注册表逐次审批，不写跨会话记忆。"""

from pydantic import BaseModel, ConfigDict, Field

from codepilot.core.context import ExecutionContext
from codepilot.tools.registry import PreparedAction, ToolError


class NoteArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    content: str = Field(min_length=1, max_length=4000)


def prepare_note(args: NoteArgs, context: ExecutionContext) -> PreparedAction:
    if context.note_saver is None or context.session_id is None:
        raise ToolError("note_requires_session")

    async def save() -> str:
        return context.note_saver(args.key, args.content)

    return PreparedAction({"session_id": context.session_id, "key": args.key, "content": args.content}, save, 5.0)
