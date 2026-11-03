"""只重建一条完整 Messages 响应；半截参数不会跨越此边界执行。"""

import asyncio
import json
from collections.abc import AsyncIterable, Callable
from typing import Any


class StreamInterrupted(ValueError):
    """连接在完整消息结束标记之前关闭。"""


async def collect_message(lines: AsyncIterable[str], on_text: Callable[[str], None]) -> dict[str, Any]:
    blocks: dict[int, dict[str, Any]] = {}
    partial: dict[int, str] = {}
    closed: set[int] = set()
    usage: dict[str, int] = {}
    stop_reason = None
    started = False
    total = 0
    text_bytes = 0
    data: list[str] = []
    async for line in lines:
        total += len(line.encode())
        if total > 1024 * 1024 or len(line.encode()) > 64 * 1024:
            raise ValueError("Model stream exceeds limit")
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
            continue
        if line or not data:
            continue
        event = json.loads("\n".join(data))
        data.clear()
        if not isinstance(event, dict):
            raise ValueError("Invalid model stream event")
        kind = event.get("type")
        if kind == "ping":
            continue
        if kind == "message_start":
            if started or event["message"].get("content"):
                raise ValueError("Invalid message start")
            started = True
            usage.update(event["message"].get("usage") or {})
        elif not started:
            raise ValueError("Missing message start")
        elif kind == "content_block_start":
            index = event["index"]
            if type(index) is not int or index != len(blocks) or index >= 32:
                raise ValueError("Invalid block index")
            block = dict(event["content_block"])
            if block.get("type") not in {"text", "tool_use", "thinking"}:
                raise ValueError("Unsupported stream block")
            blocks[index] = block
            partial[index] = ""
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            if index not in blocks or index in closed:
                raise ValueError("Delta for missing or closed block")
            block = blocks[index]
            delta_type = delta.get("type")
            if delta_type == "text_delta" and block["type"] == "text":
                text = delta["text"]
                if not isinstance(text, str):
                    raise ValueError("Invalid text delta")
                text_bytes += len(text.encode())
                if text_bytes > 16 * 1024:
                    raise ValueError("Stream text exceeds limit")
                block["text"] = block.get("text", "") + text
                on_text(text)
                await asyncio.sleep(0)
            elif delta_type == "input_json_delta" and block["type"] == "tool_use":
                partial[index] += delta["partial_json"]
            elif delta_type == "thinking_delta" and block["type"] == "thinking":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta_type == "signature_delta" and block["type"] == "thinking":
                block["signature"] = block.get("signature", "") + delta["signature"]
            else:
                raise ValueError("Unsupported stream delta")
        elif kind == "content_block_stop":
            index = event["index"]
            if index not in blocks or index in closed:
                raise ValueError("Invalid block stop")
            if partial[index]:
                if blocks[index].get("input"):
                    raise ValueError("Conflicting tool input")
                blocks[index]["input"] = json.loads(partial[index])
            closed.add(index)
        elif kind == "message_delta":
            stop_reason = event["delta"].get("stop_reason", stop_reason)
            usage.update(event.get("usage") or {})
        elif kind == "message_stop":
            if closed != set(blocks) or not isinstance(stop_reason, str):
                raise ValueError("Incomplete model message")
            return {"content": list(blocks.values()), "stop_reason": stop_reason, "usage": usage}
        else:
            raise ValueError("Unknown or failed model stream event")
    raise StreamInterrupted("Model stream ended before message_stop")
