import asyncio
import socket
from pathlib import Path
import sys
from typing import Any

import pytest
from textual.widgets import Button

import codepilot.core.app as core_app
from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock
from codepilot.core.app import CoreService
from codepilot.core.config import ModelSettings, Settings, load_model_settings
from codepilot.core.runner import Runner
import codepilot.launch as launch
from codepilot.protocol.messages import Request
from codepilot.tools.readonly import readonly_registry
from codepilot.transport.client import request
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


async def test_start_owns_core_and_releases_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        settings = Settings(port=reserved.getsockname()[1])
    env_file = tmp_path / ".env"
    env_file.write_text("CODEPILOT_CREW_BASE_URL=https://test.invalid\nCODEPILOT_CREW_MODEL=test\nCODEPILOT_CREW_API_KEY=test\n", encoding="utf-8")
    provider = FixedResponseProvider([ModelResponse(stop_reason="end_turn", content=[TextBlock(text="ready")])])
    monkeypatch.setattr(core_app, "AnthropicProvider", lambda *args, **kwargs: provider)
    async with launch.available_core(settings, tmp_path):
        result = await request(settings.host, settings.port, "agent.run", {"goal": "check"})
        assert result["answer"] == "ready"
        app = CodePilotApp(settings)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while app.query_one("#start", Button).disabled:
                    await asyncio.sleep(0.01)
            await pilot.press("f10")
    with pytest.raises((ConnectionRefusedError, TimeoutError)):
        await request(settings.host, settings.port, "core.info", {}, timeout=0.2)


async def test_start_reuses_only_matching_core(tmp_path: Path) -> None:
    runner = Runner(FixedResponseProvider([]), readonly_registry(), tmp_path)
    server = SocketServer(CoreService(runner).handle, port=0)
    await server.start()
    settings = Settings(port=server.port)
    try:
        async with launch.available_core(settings, tmp_path):
            pass
        assert (await request(settings.host, settings.port, "core.info", {}))["model_ready"]
        different = tmp_path / "different"
        different.mkdir()
        with pytest.raises(RuntimeError, match="工作区"):
            async with launch.available_core(settings, different):
                pytest.fail("unrelated workspace was accepted")
    finally:
        await server.close()


def test_setup_round_trips_hidden_key_and_protects_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["https://test.invalid", "模型"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(launch, "getpass", lambda _: "test-key-\\-secret")
    path = tmp_path / ".env"
    launch.setup_model(path)
    before = path.read_bytes()
    config = load_model_settings(path, environ={})
    assert config.api_key.get_secret_value() == "test-key-\\-secret" and config.model == "模型"
    assert "test-key" not in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="已存在"):
        launch.setup_model(path)
    assert path.read_bytes() == before


def test_missing_configuration_in_noninteractive_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "load_model_settings", lambda _: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="codepilot setup"):
        launch.start_model(tmp_path, None)


async def test_connected_but_silent_service_is_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent(_request: Request) -> dict[str, Any]:
        return await asyncio.Future()

    def unexpected_model(_workspace: Path, _env_file: Path | None) -> ModelSettings:
        pytest.fail("启动了第二个 Core")

    monkeypatch.setattr(launch, "start_model", unexpected_model)
    server = SocketServer(silent, port=0)
    await server.start()
    try:
        with pytest.raises(TimeoutError):
            async with launch.available_core(Settings(port=server.port, timeout=0.2), tmp_path):
                pytest.fail("接受了无响应的 Core")
    finally:
        await server.close()
