"""Command-line entrypoint and composition root for the MCP server."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

import httpx
import uvicorn
from pydantic import ValidationError

from . import __version__
from .application.alerts import AlertService
from .application.analytics import AnalyticsService
from .application.capabilities import CapabilityService
from .application.configuration import ConfigurationService
from .application.operations import OperationsService
from .application.saved_views import SavedViewService
from .application.service import LogService
from .infrastructure.config import ConfigError, Settings, settings_error
from .infrastructure.graylog_alerts import GraylogAlertStore
from .infrastructure.graylog_analytics import GraylogAnalyticsStore
from .infrastructure.graylog_api import build_http_client
from .infrastructure.graylog_capabilities import GraylogCapabilities
from .infrastructure.graylog_config import GraylogConfigStore
from .infrastructure.graylog_http import GraylogStore
from .infrastructure.graylog_system import GraylogSystemStore
from .infrastructure.graylog_views import GraylogViewStore
from .infrastructure.http_auth import caller_scope, caller_token, protect
from .interface.mcp_tools import HEALTH_PATH, ServiceBundle, build_server
from .interface.redaction import Redactor
from .interface.resources import CACHE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence


def wire(http: httpx.Client, cache_seconds: float = CACHE_SECONDS) -> ServiceBundle:
    """`cache_seconds` also bounds how long the adapters trust what they learned
    about the server's optional APIs."""
    capabilities = GraylogCapabilities(http, ttl=cache_seconds)
    logs = GraylogStore(http, capabilities)
    search = LogService(logs)
    return ServiceBundle(
        search=search,
        analytics=AnalyticsService(logs, GraylogAnalyticsStore(http, capabilities)),
        alerts=AlertService(GraylogAlertStore(http), search),
        views=SavedViewService(GraylogViewStore(http), search),
        operations=OperationsService(GraylogSystemStore(http)),
        configuration=ConfigurationService(GraylogConfigStore(http)),
        capabilities=CapabilityService(logs, capabilities),
    )


def _uppercase(value: str) -> str:
    return value.upper()


def parse_args(argv: Sequence[str] | None = None) -> Settings:
    """Parse CLI overrides; the environment and `.env` fill in every option not given.

    Only options the caller passed reach Settings as arguments, so an invalid
    environment value is ignored when a CLI flag replaces it.
    """
    parser = argparse.ArgumentParser(
        description="Run the Graylog MCP server.", argument_default=argparse.SUPPRESS
    )
    parser.add_argument("--base-url", help="Graylog URL")
    parser.add_argument("--api-token", help="Graylog API token")
    parser.add_argument(
        "--verify-tls",
        action=argparse.BooleanOptionalAction,
        help="Verify the Graylog TLS certificate",
    )
    parser.add_argument(
        "--allow-insecure-http",
        action=argparse.BooleanOptionalAction,
        help="Allow an http:// Graylog URL for local development",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        help="HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--log-level",
        type=_uppercase,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="MCP server log level",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", help="MCP HTTP listen host")
    parser.add_argument("--port", type=int, help="MCP HTTP listen port")
    parser.add_argument(
        "--streamable-http-path",
        help="MCP Streamable HTTP endpoint path",
    )
    parser.add_argument(
        "--enable-dns-rebinding-protection",
        action=argparse.BooleanOptionalAction,
        help="Validate Host and Origin headers for HTTP transports",
    )
    parser.add_argument(
        "--allowed-hosts",
        help="Comma-separated Host header values allowed for HTTP transports",
    )
    parser.add_argument(
        "--allowed-origins",
        help="Comma-separated Origin header values allowed for HTTP transports",
    )
    parser.add_argument(
        "--redact-fields",
        help="Comma-separated extra field names whose values are redacted",
    )
    parser.add_argument(
        "--resource-cache-seconds",
        type=float,
        help="How long MCP resource reads are cached; 0 disables caching",
    )
    parser.add_argument(
        "--auth-mode",
        type=str.lower,
        choices=("none", "token", "graylog"),
        help="HTTP client authentication: none, token (MCP_AUTH_TOKEN) or graylog "
        "(each client's own Graylog API token)",
    )
    parser.add_argument(
        "--auth-token",
        help="Shared bearer token for --auth-mode token; prefer MCP_AUTH_TOKEN, "
        "since arguments show up in the process list",
    )
    try:
        return Settings(**vars(parser.parse_args(argv))).validated()
    except ValidationError as exc:
        raise settings_error(exc) from exc


def main(argv: Sequence[str] | None = None) -> int:
    try:
        settings = parse_args(argv)
    except ConfigError as exc:
        print(f"graylog-mcp: {exc}", file=sys.stderr)
        return 1
    for warning in settings.warnings():
        print(f"graylog-mcp: warning: {warning}", file=sys.stderr)

    per_caller = settings.auth_mode == "graylog"
    with build_http_client(settings) as http:
        ok, message = check_graylog(http, settings.base_url, check_token=not per_caller)
        print(f"graylog-mcp {__version__}: {message}", file=sys.stderr)
        if not ok:
            return 1
        server = build_server(
            wire(http, settings.resource_cache_seconds),
            log_level=settings.log_level,
            resource_cache_seconds=settings.resource_cache_seconds,
            redactor=Redactor(
                settings.redact_field_names(),
                secrets=(
                    settings.api_token.get_secret_value(),
                    settings.auth_token.get_secret_value(),
                ),
                current_secret=caller_token if per_caller else lambda: None,
            ),
            caller_id=caller_scope if per_caller else lambda: "",
        )
        if settings.transport == "streamable-http":
            app = server.streamable_http_app(
                streamable_http_path=settings.streamable_http_path,
                transport_security=settings.transport_security_settings(),
                host=settings.host,
            )
            uvicorn.run(
                protect(app, settings, http, public_paths=frozenset({HEALTH_PATH})),
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
            )
        else:
            server.run(transport=settings.transport)
    return 0


def check_graylog(http: httpx.Client, base_url: str, check_token: bool) -> tuple[bool, str]:
    """Ask Graylog at startup, so a wrong URL or token fails now with a clear
    message instead of on the first tool call. Returns (start?, what to report).

    Graylog names its version at /api/ without a login, so URL and version are
    checked in every auth mode. The token is checked only when the server has one:
    in graylog auth mode each client's token is checked when it connects. An
    unreachable or failing Graylog only warns: it may be restarting, and the
    server works as soon as Graylog answers.
    """
    try:
        hello = http.get("/api/", auth=None)
        token_status = http.get("/api/system").status_code if check_token else None
    except httpx.RequestError as exc:
        return True, f"warning: cannot reach Graylog at {base_url} yet ({exc}); starting anyway"
    if hello.status_code >= 500:
        return True, (
            f"warning: Graylog at {base_url} answered HTTP {hello.status_code}; starting anyway"
        )
    try:
        version = hello.json()["version"] if hello.status_code == 200 else None
    except (ValueError, KeyError, TypeError):
        version = None
    if version is None:
        return False, (
            f"{base_url} does not look like the Graylog API (HTTP {hello.status_code} "
            "for /api/); check GRAYLOG_BASE_URL"
        )
    if token_status == 401:
        return False, f"Graylog {version} at {base_url} rejected GRAYLOG_API_TOKEN (HTTP 401)"
    tokens = "" if check_token else "; each client's Graylog token is checked when it connects"
    return True, f"connected to Graylog {version} at {base_url}{tokens}"


if __name__ == "__main__":
    sys.exit(main())
