import json
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

import httpx
import pytest

from codepilot.agent.provider import AnthropicProvider
from codepilot.core.runner import Runner
from codepilot.tools.readonly import readonly_registry


def frame(kind: str, **values: Any) -> str:
    return "event: " + kind + "\ndata: " + json.dumps({"type": kind, **values}, ensure_ascii=False) + "\n\n"


def tool_stream(*, finish: bool = True, partial: str = '{"path":"sample.txt"}') -> str:
    stream = frame("message_start", message={"content": [], "usage": {"input_tokens": 12}})
    stream += frame("content_block_start", index=0, content_block={"type": "tool_use", "id": "read-a", "name": "read_file", "input": {}})
    stream += frame("content_block_delta", index=0, delta={"type": "input_json_delta", "partial_json": partial})
    stream += frame("content_block_stop", index=0)
    if finish:
        stream += frame("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 8})
        stream += frame("message_stop")
    return stream


def text_stream() -> str:
    return (
        frame("message_start", message={"content": [], "usage": {"input_tokens": 20}})
        + frame("content_block_start", index=0, content_block={"type": "text", "text": ""})
        + frame("content_block_delta", index=0, delta={"type": "text_delta", "text": "回答"})
        + frame("ping")
        + frame("content_block_delta", index=0, delta={"type": "text_delta", "text": "完成 ✅"})
        + frame("content_block_stop", index=0)
        + frame("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 9})
        + frame("message_stop")
    )


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, text: str) -> None:
        self.data = text.encode("utf-8")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.data), 7):
            yield self.data[offset:offset + 7]


async def test_real_sse_decoder_reassembles_utf8_and_tool_input(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("actual source evidence", encoding="utf-8")
    count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal count
        body = json.loads(request.content)
        assert body["stream"] is True
        count += 1
        if count == 2:
            assert "actual source evidence" in body["messages"][-1]["content"][0]["content"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ChunkedStream(tool_stream() if count == 1 else text_stream()))

    events = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test", api_key="fake", model="fake")
        result = await Runner(provider, readonly_registry(), tmp_path).run("inspect", stream=True, on_event=events.append)
    assert result.status == "success" and result.answer == "回答完成 ✅"
    assert [item.data["text"] for item in events if item.type == "model.delta"] == ["回答", "完成 ✅"]
    assert [item.type for item in events].index("model.completed") < [item.type for item in events].index("tool.started")
    assert [item for item in events if item.type == "model.completed"][-1].data["usage"] == {"input_tokens": 20, "output_tokens": 9}


@pytest.mark.parametrize("stream", [tool_stream(finish=False), tool_stream(partial='{"path":'), tool_stream(partial='{"path":NaN}'), text_stream().replace(frame("message_stop"), "")])
async def test_partial_stream_cannot_execute_tools(tmp_path: Path, stream: str) -> None:
    events = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=ChunkedStream(stream)))) as client:
        result = await Runner(AnthropicProvider(client, base_url="https://unit.test", api_key="fake", model="fake"), readonly_registry(), tmp_path).run("inspect", stream=True, on_event=events.append)
    assert result.status == "failed" and result.stop_reason == "invalid_response"
    assert not result.tool_calls and not any(item.type == "tool.started" for item in events)
    assert events[-1].type == "run.finished"
