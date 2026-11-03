"""每次审批绑定连接、运行和工具调用，取消后不能批准旧动作。"""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from codepilot.agent.types import ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.core.permissions import ApprovalError
from codepilot.protocol.messages import RpcFault
from codepilot.transport.peer import PeerConnection


@dataclass
class PendingApproval:
    owner: PeerConnection
    future: asyncio.Future[bool]


class ApprovalBroker:
    def __init__(self, *, timeout: float = 60.0) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self.timeout = timeout

    async def request(
        self, owner: PeerConnection, call: ToolUse, preview: dict[str, Any], context: ExecutionContext
    ) -> bool:
        if context.events is None or owner.closed:
            raise ApprovalError("approval_unavailable")
        approval_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = PendingApproval(owner, future)
        status = "cancelled"
        try:
            context.events.emit("approval.requested", step=context.step, tool_use_id=call.id, approval_id=approval_id, name=call.name, parameters=preview)
            async with asyncio.timeout(self.timeout):
                allowed = await future
            status = "allowed" if allowed else "denied"
            return allowed
        except TimeoutError as exc:
            status = "timeout"
            raise ApprovalError("approval_timeout") from exc
        finally:
            self._pending.pop(approval_id, None)
            if not future.done():
                future.cancel()
            context.events.emit("approval.resolved", step=context.step, tool_use_id=call.id, approval_id=approval_id, status=status)

    def respond(self, owner: PeerConnection, approval_id: str, allow: bool) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None:
            raise RpcFault(-32013, "Approval expired or unknown")
        if pending.owner is not owner:
            raise RpcFault(-32011, "Approval belongs to another connection")
        if pending.future.done():
            return False
        pending.future.set_result(allow)
        return True
