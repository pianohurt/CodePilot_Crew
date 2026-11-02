"""命令参数、项目专用环境变量和默认值的合并边界。"""

from collections.abc import Mapping
from os import environ as process_environ
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, SecretStr, field_validator


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


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    base_url: str
    model: str = Field(min_length=1)
    model_timeout: FiniteFloat = Field(default=30.0, gt=0, le=300)
    max_tokens: int = Field(default=1024, ge=1, le=4096)

    @field_validator("api_key")
    @classmethod
    def check_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key is required")
        return value

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model is required")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Base URL must be an HTTP(S) address")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Base URL cannot contain credentials, a query or a fragment")
        return value.rstrip("/")


def load_model_settings(
    env_file: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> ModelSettings | None:
    source = process_environ if environ is None else environ
    values: dict[str, object] = {}
    fields = ("api_key", "base_url", "model", "model_timeout", "max_tokens")
    file_values: Mapping[str, str | None] = {}
    if env_file is not None:
        if not env_file.is_file():
            raise ValueError("Explicit model configuration file does not exist")
        file_values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
    for field in fields:
        key = f"CODEPILOT_CREW_{field.upper()}"
        if key in source:
            values[field] = source[key]
        elif key in file_values:
            values[field] = file_values[key]
    if not values and env_file is None:
        return None
    return ModelSettings.model_validate(values)
