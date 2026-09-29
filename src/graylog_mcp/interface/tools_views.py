"""Saved search and dashboard tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.saved_views import SavedViewService
from .responses import respond


def register(server: MCPServer, service: SavedViewService) -> None:
    @server.tool()
    def saved_searches(query: str = "", limit: int = 50) -> str:
        """List saved searches, so you can reuse queries your team already relies on.

        Args:
            query: Optional text to filter by title.
            limit: Maximum entries returned (1-200).
        """
        return respond(lambda: service.saved_searches(text=query, limit=limit))

    @server.tool()
    def run_saved_search(
        view_id: str, stream_id: str = "", limit: int = 50, fields: str = ""
    ) -> str:
        """Run a saved search with its stored query, stream and time range.

        Args:
            view_id: ID from saved_searches.
            stream_id: Optional override; required when the saved search has no stream.
            limit: Maximum messages returned (1-1000).
            fields: Optional comma-separated field names to return.
        """
        return respond(
            lambda: service.run_saved_search(
                view_id=view_id, stream_id=stream_id, limit=limit, fields=fields
            )
        )

    @server.tool()
    def dashboards(query: str = "", limit: int = 50) -> str:
        """List dashboards, which show what your team monitors.

        Args:
            query: Optional text to filter by title.
            limit: Maximum entries returned (1-200).
        """
        return respond(lambda: service.dashboards(text=query, limit=limit))
