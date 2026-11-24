"""Application settings loaded from the environment and CLI."""

from __future__ import annotations

from typing import Literal

from mcp.server.transport_security import TransportSecuritySettings
from pydantic import (
    Field,
    PositiveFloat,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..domain.errors import GraylogMcpError


class ConfigError(GraylogMcpError):
    pass


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Transport = Literal["stdio", "streamable-http"]
# none: no client check. token: clients send MCP_AUTH_TOKEN. graylog: clients send
# their own Graylog API token, which the server then uses for their Graylog calls.
AuthMode = Literal["none", "token", "graylog"]

# A shared secret shorter than this is too easy to guess (`openssl rand -hex 32` gives 64).
MIN_AUTH_TOKEN_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Loopback only, as the MCP SDK allows when it is given no security settings.
# Passing settings switches the SDK's own default off, so these must be explicit.
LOCAL_HOSTS = "127.0.0.1:*,localhost:*,[::1]:*"
LOCAL_ORIGINS = "http://127.0.0.1:*,http://localhost:*,http://[::1]:*"


class Settings(BaseSettings):
    """Configuration with environment values available as argparse defaults."""

    model_config = SettingsConfigDict(
        env_prefix="GRAYLOG_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
    )

    base_url: str = ""
    api_token: SecretStr = SecretStr("")
    verify_tls: bool = True
    allow_insecure_http: bool = False
    timeout_seconds: PositiveFloat = 30.0
    log_level: LogLevel = Field("INFO", validation_alias="MCP_LOG_LEVEL")
    transport: Transport = Field("stdio", validation_alias="MCP_TRANSPORT")
    host: str = Field("127.0.0.1", validation_alias="MCP_HOST")
    port: int = Field(8000, ge=1, le=65535, validation_alias="MCP_PORT")
    streamable_http_path: str = Field("/mcp", validation_alias="MCP_STREAMABLE_HTTP_PATH")
    enable_dns_rebinding_protection: bool = Field(
        True, validation_alias="MCP_ENABLE_DNS_REBINDING_PROTECTION"
    )
    allowed_hosts: str = Field(LOCAL_HOSTS, validation_alias="MCP_ALLOWED_HOSTS")
    allowed_origins: str = Field(LOCAL_ORIGINS, validation_alias="MCP_ALLOWED_ORIGINS")
    redact_fields: str = Field("", validation_alias="MCP_REDACT_FIELDS")
    resource_cache_seconds: float = Field(
        60.0, ge=0, validation_alias="MCP_RESOURCE_CACHE_SECONDS"
    )
    auth_mode: AuthMode = Field("none", validation_alias="MCP_AUTH_MODE")
    auth_token: SecretStr = Field(SecretStr(""), validation_alias="MCP_AUTH_TOKEN")

    @field_validator(
        "base_url",
        "api_token",
        "auth_token",
        "host",
        "streamable_http_path",
        "allowed_hosts",
        "allowed_origins",
        "redact_fields",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "verify_tls",
        "allow_insecure_http",
        "enable_dns_rebinding_protection",
        "allowed_hosts",
        "allowed_origins",
        mode="before",
    )
    @classmethod
    def blanks_use_defaults(cls, value: object, info: ValidationInfo) -> object:
        """A blank value (such as `--allowed-hosts ""`) means the default, not "none"."""
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def blank_timeout_uses_default(cls, value: str) -> float | str:
        return 30.0 if isinstance(value, str) and not value.strip() else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("auth_mode", mode="before")
    @classmethod
    def normalize_auth_mode(cls, value: str) -> str:
        return value.strip().lower() or "none"

    @field_validator("streamable_http_path")
    @classmethod
    def require_absolute_http_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("streamable_http_path must start with '/'")
        return value.rstrip("/") or "/"

    def validated(self) -> Settings:
        """Apply checks that depend on more than one setting."""
        base_url = self.base_url.rstrip("/")
        required = [("GRAYLOG_BASE_URL", base_url)]
        if self.auth_mode != "graylog":  # there, every client brings its own Graylog token
            required.append(("GRAYLOG_API_TOKEN", self.api_token.get_secret_value()))
        missing = [name for name, value in required if not value]
        if missing:
            raise ConfigError(f"missing required environment variable(s): {', '.join(missing)}")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("GRAYLOG_BASE_URL must start with http:// or https://")
        if base_url.startswith("http://") and not self.allow_insecure_http:
            raise ConfigError(
                "GRAYLOG_BASE_URL must use https://; set GRAYLOG_ALLOW_INSECURE_HTTP=true "
                "only for local development"
            )
        if self.auth_mode != "none" and self.transport != "streamable-http":
            raise ConfigError("MCP_AUTH_MODE applies only to MCP_TRANSPORT=streamable-http")
        if (
            self.auth_mode == "token"
            and len(self.auth_token.get_secret_value()) < MIN_AUTH_TOKEN_LENGTH
        ):
            raise ConfigError(
                f"MCP_AUTH_MODE=token needs an MCP_AUTH_TOKEN of at least "
                f"{MIN_AUTH_TOKEN_LENGTH} characters, e.g. from `openssl rand -hex 32`"
            )
        return self.model_copy(update={"base_url": base_url})

    def warnings(self) -> list[str]:
        """Settings that work but are probably not what the operator wants."""
        found = []
        if (
            self.transport == "streamable-http"
            and self.auth_mode == "none"
            and self.host not in LOOPBACK_HOSTS
        ):
            found.append(
                f"the HTTP endpoint listens on {self.host} without authentication; "
                "set MCP_AUTH_MODE=token or graylog"
            )
        if self.auth_mode == "graylog" and self.api_token.get_secret_value():
            found.append(
                "GRAYLOG_API_TOKEN is not used with MCP_AUTH_MODE=graylog: "
                "each client's own token is"
            )
        return found

    def transport_security_settings(self) -> TransportSecuritySettings:
        """Build the SDK security policy from comma-separated CLI/environment values."""
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=self.enable_dns_rebinding_protection,
            allowed_hosts=_split_csv(self.allowed_hosts),
            allowed_origins=_split_csv(self.allowed_origins),
        )

    def redact_field_names(self) -> list[str]:
        return _split_csv(self.redact_fields)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def settings_error(exc: ValidationError) -> ConfigError:
    """Turn Pydantic's structured errors into the application's config error."""
    messages = "; ".join(
        f"{'.'.join(map(str, error['loc']))}: {error['msg']}" if error["loc"] else error["msg"]
        for error in exc.errors()
    )
    return ConfigError(messages)
