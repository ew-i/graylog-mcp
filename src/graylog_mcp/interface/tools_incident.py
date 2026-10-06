"""Composite incident investigation workflow."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.incident import IncidentService
from .responses import respond


def register(server: MCPServer, service: IncidentService) -> None:
    @server.tool()
    def investigate_incident(
        query: str,
        stream_id: str,
        range_seconds: int = 3600,
        baseline_offset_seconds: int = 86400,
        alert_limit: int = 20,
        message_limit: int = 5,
        top_n: int = 10,
        context_field: str = "source",
        context_before: int = 3,
        context_after: int = 3,
        context_window_seconds: int = 300,
    ) -> str:
        """Gather cluster health, alerts, baseline volume, log patterns, messages,
        nearby context, and stream/pipeline configuration for one incident query.

        Args:
            query: Lucene query selecting the incident messages.
            stream_id: Stream to investigate (from browse_streams).
            range_seconds: Current incident look-back window (default 1 h).
            baseline_offset_seconds: How far back the comparison baseline ends.
            alert_limit: Maximum recent alerts to include (1-200).
            message_limit: Maximum representative messages to include (1-1000).
            top_n: Number of values to include for each common log field (1-100).
            context_field: Field used to scope nearby messages, usually source or pod.
            context_before: Messages before the newest representative message (0-50).
            context_after: Messages after the newest representative message (0-50).
            context_window_seconds: How far around that message to search.
        """
        return respond(
            lambda: service.investigate(
                query=query,
                stream_id=stream_id,
                range_seconds=range_seconds,
                baseline_offset_seconds=baseline_offset_seconds,
                alert_limit=alert_limit,
                message_limit=message_limit,
                top_n=top_n,
                context_field=context_field,
                context_before=context_before,
                context_after=context_after,
                context_window_seconds=context_window_seconds,
            )
        )
