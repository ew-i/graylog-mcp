"""MCP adapter: exposes the application's use cases as tools, resources and prompts.

Each area registers its own tools; none of them raise. Failures become
`{"error": ..., "kind": ...}` payloads the model can read and react to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse

from .. import __version__
from ..application.incident import IncidentService
from . import (
    audit,
    prompts,
    redaction,
    resources,
    tools_alerts,
    tools_analytics,
    tools_capabilities,
    tools_configuration,
    tools_incident,
    tools_operations,
    tools_search,
    tools_views,
)
from .responses import Responder

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from ..application.alerts import AlertService
    from ..application.analytics import AnalyticsService
    from ..application.capabilities import CapabilityService
    from ..application.configuration import ConfigurationService
    from ..application.operations import OperationsService
    from ..application.saved_views import SavedViewService
    from ..application.service import LogService


@dataclass(frozen=True)
class ServiceBundle:
    search: LogService
    analytics: AnalyticsService
    alerts: AlertService
    views: SavedViewService
    operations: OperationsService
    configuration: ConfigurationService
    capabilities: CapabilityService


INSTRUCTIONS = (
    "Read-only Graylog access. Call browse_streams first: "
    "searches and analyses need a stream_id. For troubleshooting, "
    "recent_alerts, count_over_time and compare_windows are good starting points. "
    "server_capabilities says which tools this Graylog version cannot serve."
)
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# Liveness for container orchestration; http_auth lets it through without a token.
HEALTH_PATH = "/healthz"


def build_server(
    services: ServiceBundle,
    name: str = "graylog",
    log_level: LogLevel = "INFO",
    resource_cache_seconds: float = resources.CACHE_SECONDS,
    redactor: redaction.Redactor | None = None,
    caller_id: Callable[[], str] = lambda: "",
) -> MCPServer:
    """`caller_id` names the current caller when callers see different Graylog data
    (each with their own token): cached resources stay per caller, and the audit
    log says who called a tool."""
    redactor = redactor or redaction.Redactor()
    server = MCPServer(
        name,
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=log_level,
        middleware=[audit.tool_call_logger(redactor, caller_id)],
    )

    @server.custom_route(HEALTH_PATH, methods=["GET"])  # type: ignore[untyped-decorator]  # SDK
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    respond = Responder(redactor)
    tools_search.register(server, services.search, respond)
    tools_analytics.register(server, services.analytics, respond)
    tools_alerts.register(server, services.alerts, respond)
    tools_views.register(server, services.views, respond)
    tools_operations.register(server, services.operations, respond)
    tools_configuration.register(server, services.configuration, respond)
    tools_capabilities.register(server, services.capabilities, respond)
    resources.register(
        server, services, respond, resources.TtlCache(resource_cache_seconds, scope=caller_id)
    )
    prompts.register(server)
    tools_incident.register(
        server,
        IncidentService(
            search=services.search,
            analytics=services.analytics,
            alerts=services.alerts,
            operations=services.operations,
            configuration=services.configuration,
        ),
        respond,
    )
    return server
