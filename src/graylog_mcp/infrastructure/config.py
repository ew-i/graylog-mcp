"""Application settings loaded from the environment and CLI."""

from __future__ import annotations

from typing import Literal

from pydantic import PositiveFloat, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..domain.errors import GraylogMcpError


class ConfigError(GraylogMcpError):
    pass


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Configuration with environment values available as argparse defaults."""

    model_config = SettingsConfigDict(
        env_prefix="GRAYLOG_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        validate_default=True,
    )

    base_url: str = ""
    api_token: str = ""
    verify_tls: bool = True
    allow_insecure_http: bool = False
    timeout_seconds: PositiveFloat = 30.0
    log_level: LogLevel = "INFO"

    @field_validator("base_url", "api_token", mode="before")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("verify_tls", "allow_insecure_http", mode="before")
    @classmethod
    def blank_booleans_use_defaults(cls, value: str, info: ValidationInfo) -> bool | str:
        if isinstance(value, str) and not value.strip():
            return info.field_name == "verify_tls"
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def blank_timeout_uses_default(cls, value: str) -> float | str:
        return 30.0 if isinstance(value, str) and not value.strip() else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    def validated(self) -> Settings:
        """Apply checks that depend on more than one setting."""
        base_url = self.base_url.rstrip("/")
        token = self.api_token
        missing = [
            n for n, v in (("GRAYLOG_BASE_URL", base_url), ("GRAYLOG_API_TOKEN", token)) if not v
        ]
        if missing:
            raise ConfigError(f"missing required environment variable(s): {', '.join(missing)}")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("GRAYLOG_BASE_URL must start with http:// or https://")
        if base_url.startswith("http://") and not self.allow_insecure_http:
            raise ConfigError(
                "GRAYLOG_BASE_URL must use https://; set GRAYLOG_ALLOW_INSECURE_HTTP=true "
                "only for local development"
            )
        return self.model_copy(update={"base_url": base_url, "api_token": token})


def settings_error(exc: ValidationError) -> ConfigError:
    """Turn Pydantic's structured errors into the application's config error."""
    messages = "; ".join(error["msg"] for error in exc.errors())
    return ConfigError(messages)
