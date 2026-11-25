"""Read-only configuration tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import CallToolResult  # noqa: TC002 - the MCP SDK reads tool annotations

from .tool_annotations import READ_ONLY

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from ..application.configuration import ConfigurationService
    from .responses import Responder


def register(server: MCPServer, service: ConfigurationService, respond: Responder) -> None:
    @server.tool(title="Stream rules", annotations=READ_ONLY)
    def stream_rules(stream_id: str) -> CallToolResult:
        """Which messages a stream receives: its routing rules, in plain words.

        Args:
            stream_id: Stream to inspect (from browse_streams).
        """
        return respond(lambda: service.stream_rules(stream_id=stream_id))

    @server.tool(title="Pipeline rules", annotations=READ_ONLY)
    def pipeline_rules(stream_id: str = "", include_source: bool = True) -> CallToolResult:
        """Processing pipelines (all, or those connected to one stream) with their
        stages and rules. These can rename, drop or add fields.

        Args:
            stream_id: Optional stream to limit to its connected pipelines.
            include_source: Include each rule's source code.
        """
        return respond(
            lambda: service.pipeline_rules(stream_id=stream_id, include_source=include_source)
        )

    @server.tool(title="Index sets", annotations=READ_ONLY)
    def index_sets(stream_id: str = "") -> CallToolResult:
        """Index sets with rotation and retention settings, which tell you how far
        back logs can be searched.

        Args:
            stream_id: Optional stream; marks and lists first the index set it writes to.
        """
        return respond(lambda: service.index_sets(stream_id=stream_id))

    @server.tool(title="Lookup tables", annotations=READ_ONLY)
    def lookup_tables(query: str = "", limit: int = 50, next_cursor: str = "") -> CallToolResult:
        """List lookup tables (e.g. IP to hostname, user ID to name) that lookup_value can use.

        Args:
            query: Optional text to filter by name.
            limit: Maximum tables returned (1-200).
            next_cursor: A previous response's next_cursor. Continues that exact request:
                filters, time range and page size come from the cursor.
        """
        return respond(
            lambda: service.lookup_tables(text=query, limit=limit, next_cursor=next_cursor)
        )

    @server.tool(title="Look up value", annotations=READ_ONLY)
    def lookup_value(table: str, key: str) -> CallToolResult:
        """Look up a key in a lookup table.

        Args:
            table: Lookup table name (from lookup_tables).
            key: Key to resolve.
        """
        return respond(lambda: service.lookup_value(table=table, key=key), fields=(table,))
