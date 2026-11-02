import math

import pytest
from pydantic import ValidationError

from codepilot.core.config import load_settings


def test_defaults_and_explicit_overrides() -> None:
    assert load_settings(environ={}).port == 7438
    settings = load_settings(
        port=9001,
        environ={"CODEPILOT_CREW_PORT": "invalid", "CODEPILOT_CREW_TIMEOUT": "1.5"},
    )
    assert settings.port == 9001
    assert settings.timeout == 1.5


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_ports_fail_before_network_io(port: int) -> None:
    with pytest.raises(ValidationError):
        load_settings(port=port, environ={})


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan])
def test_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValidationError):
        load_settings(timeout=timeout, environ={})


def test_project_namespace_does_not_reuse_old_configuration() -> None:
    settings = load_settings(environ={"KAMA_PORT": "7437", "CODEPILOT_CREW_LOG_LEVEL": "debug"})
    assert settings.port == 7438
    assert settings.log_level == "DEBUG"
