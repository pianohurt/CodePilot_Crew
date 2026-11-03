"""JSON-RPC 风格子集：要求字符串 id，不接收通知或批量请求。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
FRAME_TOO_LARGE = -32001

type RequestId = Annotated[str, Field(min_length=1)]


class StrictMessage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Request(StrictMessage):
    jsonrpc: Literal["2.0"]
    id: RequestId
    method: Annotated[str, Field(min_length=1)]
    params: dict[str, Any]


class ErrorBody(StrictMessage):
    code: int
    message: str


class Success(StrictMessage):
    jsonrpc: Literal["2.0"]
    id: RequestId
    result: dict[str, Any]


class Failure(StrictMessage):
    jsonrpc: Literal["2.0"]
    id: RequestId | None
    error: ErrorBody


class PingResult(StrictMessage):
    message: Literal["pong"]
    server_version: Annotated[str, Field(min_length=1)]


class AgentRunParams(StrictMessage):
    goal: Annotated[str, Field(min_length=1, max_length=8000)]
    max_steps: Annotated[int, Field(ge=1, le=32)] = 8


class CancelParams(StrictMessage):
    run_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class ApprovalResponseParams(StrictMessage):
    approval_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    allow: bool


RESPONSE = TypeAdapter(Success | Failure)


class RpcFault(Exception):
    """只携带可公开的业务错误；内部异常另行写日志。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def failure(request_id: str | None, code: int, message: str) -> Failure:
    return Failure(jsonrpc="2.0", id=request_id, error=ErrorBody(code=code, message=message))
