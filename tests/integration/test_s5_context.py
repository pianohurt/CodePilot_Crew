import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider, request_cost
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolUse
from codepilot.core.runner import Runner
from codepilot.session.history import validate_history
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.readonly import readonly_registry


@pytest.mark.parametrize("mode", ["continue", "empty", "upstream_error"])
async def test_restored_history_is_compacted_without_mutating_originals(tmp_path: Path, mode: str) -> None:
    directory = tmp_path / ".codepilot"
    store = SessionStore(directory, tmp_path)
    (tmp_path / "sample.txt").write_text("ORIGINAL_OUTPUT_" + "x" * 6000 + "_END_MARKER", encoding="utf-8")
    try:
        session_id = store.create()["id"]
        scripted = [
            ModelResponse(content=[ToolUse(id="old-read", name="read_file", input={"path": "sample.txt"})], stop_reason="tool_use"),
            *[ModelResponse(content=[TextBlock(text="read only; checks pending\n" + "x" * 2400)], stop_reason="end_turn") for _ in range(10)],
        ]
        runner = Runner(FixedResponseProvider(scripted), readonly_registry(), tmp_path, sessions=SessionManager(store))
        originals = []
        for _ in range(10):
            result = await runner.run("Keep files unchanged. Tests have not been run.", session_id=session_id)
            assert result.status == "success"
            originals.append((result.run_id, store.records(result.run_id)))
    finally:
        store.close()

    store = SessionStore(directory, tmp_path)
    summaries = []
    primary = []
    budget = 10500

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request_cost(payload) <= budget
        if not payload["tools"]:
            summaries.append(payload)
            if mode == "upstream_error":
                return httpx.Response(503)
            handoff = {
                "goal": "Inspect the workspace",
                "constraints": ["Keep files unchanged"],
                "completed": ["Read sample.txt"],
                "files": ["sample.txt unchanged"],
                "pending": ["Run tests if requested"],
                "verification": ["Tests have not been run"],
            }
            text = "" if mode == "empty" else json.dumps(handoff)
        else:
            primary.append(payload)
            validate_history([Message.model_validate(message) for message in payload["messages"]])
            text = "Files unchanged. Tests have not been run."
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = AnthropicProvider(client, base_url="https://test.invalid", api_key="fake", model="fake", context_budget=budget)
            runner = Runner(provider, readonly_registry(), tmp_path, sessions=SessionManager(store))
            result = await runner.run("Report the verification status without changing files.", session_id=session_id)
        assert summaries
        if mode == "continue":
            assert result.status == "success" and len(summaries) >= 2
            assert len(primary) == 1
            prompt = json.dumps(primary[0]["messages"])
            assert "Keep files unchanged" in prompt and "Tests have not been run" in prompt
            assert "Report the verification status without changing files." in prompt
            assert "_END_MARKER" not in json.dumps(summaries)
        else:
            assert result.stop_reason == "context_budget" and not primary
        assert all(store.records(run_id) == records for run_id, records in originals)
        history, _ = store.load(session_id)
        assert any(getattr(block, "content", "").endswith("_END_MARKER") for item in history for block in item.content)
    finally:
        store.close()


async def test_recent_tool_observation_is_clipped_before_next_request(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("readable\n" + "x" * 6800, encoding="utf-8")
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request_cost(body) <= 8192
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "a", "name": "read_file", "input": {"path": "sample.txt"}}], "stop_reason": "tool_use"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "finished"}], "stop_reason": "end_turn"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(client, base_url="https://test.invalid", api_key="fake", model="fake", context_budget=8192)
        result = await Runner(provider, readonly_registry(), tmp_path).run("Read sample.txt")
    assert result.status == "success" and len(requests) == 2
    block = requests[1]["messages"][-1]["content"][0]
    assert block["tool_use_id"] == "a" and not block["is_error"]
    assert len(block["content"].encode()) <= 2048 and block["content"].endswith("[truncated]")
