"""Read-only configuration tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.configuration import ConfigurationService
from .responses import respond


def register(server: MCPServer, service: ConfigurationService) -> None:
    @server.tool()
    def stream_rules(stream_id: str) -> str:
        """Which messages a stream receives: its routing rules, in plain words.

        Args:
            stream_id: Stream to inspect (from browse_streams).
        """
        return respond(lambda: service.stream_rules(stream_id=stream_id))

    @server.tool()
    def pipeline_rules(stream_id: str = "", include_source: bool = True) -> str:
        """Processing pipelines (all, or those connected to one stream) with their
        stages and rules. These can rename, drop or add fields.

        Args:
            stream_id: Optional stream to limit to its connected pipelines.
            include_source: Include each rule's source code.
        """
        return respond(
            lambda: service.pipeline_rules(stream_id=stream_id, include_source=include_source)
        )

    @server.tool()
    def index_sets(stream_id: str = "") -> str:
        """Index sets with rotation and retention settings, which tell you how far
        back logs can be searched.

        Args:
            stream_id: Optional stream; marks and lists first the index set it writes to.
        """
        return respond(lambda: service.index_sets(stream_id=stream_id))

    @server.tool()
    def lookup_tables(query: str = "", limit: int = 50) -> str:
        """List lookup tables (e.g. IP to hostname, user ID to name) that lookup_value can use.

        Args:
            query: Optional text to filter by name.
            limit: Maximum tables returned (1-200).
        """
        return respond(lambda: service.lookup_tables(text=query, limit=limit))

    @server.tool()
    def lookup_value(table: str, key: str) -> str:
        """Look up a key in a lookup table.

        Args:
            table: Lookup table name (from lookup_tables).
            key: Key to resolve.
        """
        return respond(lambda: service.lookup_value(table=table, key=key))
