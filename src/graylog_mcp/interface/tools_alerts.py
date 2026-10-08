"""Alert tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.alerts import AlertService
from .responses import respond


def register(server: MCPServer, service: AlertService) -> None:
    @server.tool()
    def recent_alerts(
        range_seconds: int = 86400,
        limit: int = 50,
        alerts_only: bool = True,
        query: str = "",
        definition_id: str = "",
        next_cursor: str = "",
    ) -> str:
        """Alerts (and optionally plain events) raised by Graylog's event definitions,
        newest first. A good first step when troubleshooting.

        Args:
            range_seconds: Look-back window (default 24 h).
            limit: Maximum events returned (1-200).
            alerts_only: True for alerts only; False to include non-alert events.
            query: Optional search within event messages.
            definition_id: Optional event definition ID to filter by (see alert_definitions).
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.recent_alerts(
                seconds=range_seconds,
                limit=limit,
                alerts_only=alerts_only,
                text=query,
                definition_id=definition_id,
                next_cursor=next_cursor,
            )
        )

    @server.tool()
    def alert_definitions(query: str = "", limit: int = 50, next_cursor: str = "") -> str:
        """The configured event/alert definitions: what each checks for (query and
        streams), how often it runs, and its priority.

        Args:
            query: Optional text to filter definitions by title.
            limit: Maximum definitions returned (1-200).
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.alert_definitions(text=query, limit=limit, next_cursor=next_cursor)
        )
