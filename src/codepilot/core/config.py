"""命令参数、项目专用环境变量和默认值的合并边界。"""

from collections.abc import Mapping
from os import environ as process_environ
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=7438, ge=1, le=65535)
    timeout: FiniteFloat = Field(default=5.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


def load_settings(
    *,
    port: int | None = None,
    timeout: float | None = None,
    log_level: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    source = process_environ if environ is None else environ
    values: dict[str, object] = {}
    overrides = {"port": port, "timeout": timeout, "log_level": log_level}
    for field, explicit in overrides.items():
        value = explicit if explicit is not None else source.get(f"CODEPILOT_CREW_{field.upper()}")
        if value is not None:
            values[field] = value.upper() if field == "log_level" and isinstance(value, str) else value
    return Settings.model_validate(values)
