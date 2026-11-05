"""Alert tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.alerts import AlertService
from .responses import Responder


def register(server: MCPServer, service: AlertService, respond: Responder) -> None:
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
    def alert_details(event_id: str, limit: int = 20) -> str:
        """Everything needed to triage one alert in a single call: the event, its
        definition (query, streams, window), the message that fired it (filter
        alerts) and the messages matching the definition query in the event's
        time range, per source stream and narrowed to its group-by values.

        Args:
            event_id: The event's id from recent_alerts.
            limit: Maximum matching messages per stream (1-1000).
        """
        return respond(lambda: service.alert_details(event_id=event_id, limit=limit))

    @server.tool()
    def find_related_alerts(event_id: str, range_seconds: int = 3600, limit: int = 50) -> str:
        """Other events (alerts or not) around an event's time that share its
        definition or any of its group-by values, e.g. the same host alerting under
        another definition. Each says why it is related (related_by).

        Args:
            event_id: The event's id from recent_alerts.
            range_seconds: How far before and after the event to look (default 1 h).
            limit: Maximum related events returned (1-200).
        """
        return respond(
            lambda: service.find_related_alerts(
                event_id=event_id, seconds=range_seconds, limit=limit
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
