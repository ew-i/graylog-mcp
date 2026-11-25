"""Saved search and dashboard tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import CallToolResult  # noqa: TC002 - the MCP SDK reads tool annotations

from .tool_annotations import READ_ONLY

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from ..application.saved_views import SavedViewService
    from .responses import Responder


def register(server: MCPServer, service: SavedViewService, respond: Responder) -> None:
    @server.tool(title="Saved searches", annotations=READ_ONLY)
    def saved_searches(query: str = "", limit: int = 50, next_cursor: str = "") -> CallToolResult:
        """List saved searches, so you can reuse queries your team already relies on.

        Args:
            query: Optional text to filter by title.
            limit: Maximum entries returned (1-200).
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.saved_searches(text=query, limit=limit, next_cursor=next_cursor)
        )

    @server.tool(title="Run saved search", annotations=READ_ONLY)
    def run_saved_search(
        view_id: str,
        stream_id: str = "",
        limit: int = 50,
        fields: tuple[str, ...] = (),
        next_cursor: str = "",
    ) -> CallToolResult:
        """Run a saved search with its stored query, stream and time range.

        Args:
            view_id: ID from saved_searches.
            stream_id: Optional override; required when the saved search has no stream.
            limit: Maximum messages returned (1-1000).
            fields: Optional field names to return, e.g. ["source", "message"].
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.run_saved_search(
                view_id=view_id,
                stream_id=stream_id,
                limit=limit,
                fields=fields,
                next_cursor=next_cursor,
            )
        )

    @server.tool(title="Dashboards", annotations=READ_ONLY)
    def dashboards(query: str = "", limit: int = 50, next_cursor: str = "") -> CallToolResult:
        """List dashboards, which show what your team monitors.

        Args:
            query: Optional text to filter by title.
            limit: Maximum entries returned (1-200).
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.dashboards(text=query, limit=limit, next_cursor=next_cursor)
        )
