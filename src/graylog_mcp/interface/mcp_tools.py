"""MCP adapter: exposes the application's use cases as tools.

Each area registers its own tools; none of them raise. Expected failures become
`{"error": ..., "kind": ...}` payloads the model can read and react to.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer

from ..application.alerts import AlertService
from ..application.analytics import AnalyticsService
from ..application.configuration import ConfigurationService
from ..application.operations import OperationsService
from ..application.saved_views import SavedViewService
from ..application.service import LogService
from . import (
    tools_alerts,
    tools_analytics,
    tools_configuration,
    tools_operations,
    tools_search,
    tools_views,
)


@dataclass(frozen=True)
class ServiceBundle:
    search: LogService
    analytics: AnalyticsService
    alerts: AlertService
    views: SavedViewService
    operations: OperationsService
    configuration: ConfigurationService


INSTRUCTIONS = (
    "Read-only Graylog access. Call browse_streams first: "
    "searches and analyses need a stream_id. For troubleshooting, "
    "recent_alerts, count_over_time and compare_windows are good starting points."
)


def build_server(services: ServiceBundle, name: str = "graylog") -> MCPServer:
    server = MCPServer(name, instructions=INSTRUCTIONS)
    tools_search.register(server, services.search)
    tools_analytics.register(server, services.analytics)
    tools_alerts.register(server, services.alerts)
    tools_views.register(server, services.views)
    tools_operations.register(server, services.operations)
    tools_configuration.register(server, services.configuration)
    return server
