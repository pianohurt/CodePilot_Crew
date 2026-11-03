"""写入和恢复均检查配对；不把中断的半批调用伪造成可续跑历史。"""

from collections.abc import Sequence

from codepilot.agent.types import Message, ToolResult, ToolUse


def validate_history(messages: Sequence[Message]) -> set[str]:
    seen: set[str] = set()
    pending: list[str] = []
    for message in messages:
        calls = [block.id for block in message.content if isinstance(block, ToolUse)]
        results = [block.tool_use_id for block in message.content if isinstance(block, ToolResult)]
        if pending:
            if message.role != "user" or results != pending or len(results) != len(message.content):
                raise ValueError("Unpaired tool calls")
            pending = []
        elif results:
            raise ValueError("Orphan tool results")
        if calls:
            if len(calls) != len(set(calls)) or seen.intersection(calls):
                raise ValueError("Repeated tool call id")
            seen.update(calls)
            pending = calls
    if pending:
        raise ValueError("Incomplete tool batch")
    return seen
