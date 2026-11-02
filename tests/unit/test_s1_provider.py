import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from codepilot.agent.provider import AnthropicProvider, ProviderError, message_endpoint, parse_response
from codepilot.agent.types import Message, TextBlock
from codepilot.core.config import load_model_settings
from codepilot.tools.readonly import readonly_registry


async def test_messages_request_shape_and_complete_response() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "unit-test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["stream"] is False and body["model"] == "test-model"
        assert body["messages"][0]["content"][0]["text"] == "inspect"
        assert {item["name"] for item in body["tools"]} == {"read_file", "list_dir"}
        return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "a", "name": "list_dir", "input": {}}], "stop_reason": "tool_use"})

    registry = readonly_registry()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test/v1", api_key="unit-test-key", model="test-model")
        result = await provider.complete([Message(role="user", content=[TextBlock(text="inspect")])], registry.definitions(registry.names))
    assert result.content[0].id == "a" and result.stop_reason == "tool_use"


@pytest.mark.parametrize("base", ["https://unit.test", "https://unit.test/v1", "https://unit.test/v1/messages", "https://unit.test/v1/"])
def test_supported_base_url_forms(base: str) -> None:
    assert message_endpoint(base) == "https://unit.test/v1/messages"


async def test_opaque_thinking_block_is_preserved_for_next_request() -> None:
    parsed = parse_response({"content": [
        {"type": "thinking", "thinking": "opaque-test-content", "signature": "opaque-test-signature"},
        {"type": "text", "text": "answer"},
    ], "stop_reason": "end_turn"})

    def handle(request: httpx.Request) -> httpx.Response:
        content = json.loads(request.content)["messages"][0]["content"][0]
        assert content == {"type": "thinking", "thinking": "opaque-test-content", "signature": "opaque-test-signature"}
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test", api_key="unused", model="test")
        await provider.complete([Message(role="assistant", content=parsed.content)], [])


@pytest.mark.parametrize(
    "body",
    [
        {"content": [{"type": "tool_use", "id": "a", "name": "read_file", "input": "{partial"}], "stop_reason": "tool_use"},
        {"content": [{"type": "tool_use", "name": "read_file", "input": {}}], "stop_reason": "tool_use"},
        {"content": [{"type": "text"}], "stop_reason": "end_turn"},
        {"content": [{"type": "unknown"}], "stop_reason": "end_turn"},
    ],
)
async def test_partial_or_unsupported_response_is_provider_error(body: dict[str, object]) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test", api_key="unused", model="test")
        with pytest.raises(ProviderError) as error:
            await provider.complete([], [])
        assert error.value.invalid_response


async def test_remote_error_does_not_echo_secret_body_and_no_retry() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="sensitive-key-from-remote")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test", api_key="unused", model="test")
        with pytest.raises(ProviderError, match="HTTP 401") as error:
            await provider.complete([], [])
        assert "sensitive" not in str(error.value)
    assert calls == 1


async def test_timeout_is_classified() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private endpoint detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = AnthropicProvider(client, base_url="https://unit.test", api_key="unused", model="test")
        with pytest.raises(ProviderError, match="timed out"):
            await provider.complete([], [])


def test_explicit_config_file_and_environment_precedence(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CODEPILOT_CREW_API_KEY=unit-secret\nCODEPILOT_CREW_BASE_URL=https://unit.test\nCODEPILOT_CREW_MODEL=file-model\n", encoding="utf-8-sig")
    settings = load_model_settings(env_file, environ={"CODEPILOT_CREW_MODEL": "environment-model"})
    assert settings.model == "environment-model"
    assert "unit-secret" not in repr(settings)
    assert load_model_settings(environ={}) is None
    with pytest.raises(ValueError):
        load_model_settings(tmp_path / "absent", environ={})


@pytest.mark.parametrize("url", ["file:///local", "https://user:password@unit.test", "https://unit.test?key=secret", "https://unit.test#fragment"])
def test_config_rejects_credential_bearing_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        load_model_settings(environ={"CODEPILOT_CREW_API_KEY": "unit", "CODEPILOT_CREW_BASE_URL": url, "CODEPILOT_CREW_MODEL": "unit"})
