"""Settings loaded from the environment, validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.errors import GraylogMcpError


class ConfigError(GraylogMcpError):
    pass


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_token: str
    verify_tls: bool = True
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        base_url = env.get("GRAYLOG_BASE_URL", "").strip().rstrip("/")
        token = env.get("GRAYLOG_API_TOKEN", "").strip()
        missing = [
            n for n, v in (("GRAYLOG_BASE_URL", base_url), ("GRAYLOG_API_TOKEN", token)) if not v
        ]
        if missing:
            raise ConfigError(f"missing required environment variable(s): {', '.join(missing)}")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("GRAYLOG_BASE_URL must start with http:// or https://")

        raw_timeout = env.get("GRAYLOG_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else 30.0
        except ValueError:
            raise ConfigError("GRAYLOG_TIMEOUT_SECONDS must be a number") from None
        if timeout <= 0:
            raise ConfigError("GRAYLOG_TIMEOUT_SECONDS must be positive")

        return cls(
            base_url=base_url,
            api_token=token,
            verify_tls=_flag(env, "GRAYLOG_VERIFY_TLS", default=True),
            timeout_seconds=timeout,
        )
