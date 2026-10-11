"""Command-line entrypoint and composition root for the MCP server."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import httpx
from pydantic import ValidationError

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
from .interface.mcp_tools import ServiceBundle, build_server
from .interface.redaction import Redactor
from .interface.resources import CACHE_SECONDS


def wire(http: httpx.Client, cache_seconds: float = CACHE_SECONDS) -> ServiceBundle:
    """`cache_seconds` also bounds how long the adapters trust what they learned
    about the server's optional APIs."""
    capabilities = GraylogCapabilities(http, ttl=cache_seconds)
    logs = GraylogStore(http, capabilities)
    search = LogService(logs)
    return ServiceBundle(
        search=search,
        analytics=AnalyticsService(logs, GraylogAnalyticsStore(http, capabilities)),
        alerts=AlertService(GraylogAlertStore(http)),
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

    with build_http_client(settings) as http:
        server = build_server(
            wire(http, settings.resource_cache_seconds),
            log_level=settings.log_level,
            resource_cache_seconds=settings.resource_cache_seconds,
            redactor=Redactor(settings.redact_field_names(), secrets=(settings.api_token,)),
        )
        if settings.transport == "streamable-http":
            server.run(
                transport=settings.transport,
                host=settings.host,
                port=settings.port,
                streamable_http_path=settings.streamable_http_path,
                transport_security=settings.transport_security_settings(),
            )
        else:
            server.run(transport=settings.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
