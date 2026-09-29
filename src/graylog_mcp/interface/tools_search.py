"""Search tools: streams, message search and lookup, sampled field ranking."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.service import LogService
from .responses import respond as _respond


def register(server: MCPServer, service: LogService) -> None:
    @server.tool()
    def cluster_status() -> str:
        """Report Graylog version, node/cluster identity, hostname, timezone and
        whether message processing is running."""
        return _respond(service.cluster_info)

    @server.tool()
    def browse_streams() -> str:
        """List the log streams you may query, with their IDs.

        Call this before any search: every search, aggregation and field
        lookup needs a stream_id from this list."""
        return _respond(service.streams)

    @server.tool()
    def find_recent_logs(
        query: str,
        stream_id: str,
        range_seconds: int = 900,
        limit: int = 50,
        fields: str = "",
        sort: str = "timestamp",
        sort_order: str = "desc",
    ) -> str:
        """Search messages from the last N seconds.

        Args:
            query: Lucene query, e.g. 'level:error AND source:api'.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (1 s to 30 days, default 15 min).
            limit: Maximum messages returned (1-1000).
            fields: Optional comma-separated field names to return.
            sort: Field to sort on.
            sort_order: 'asc' or 'desc'.
        """
        return _respond(
            lambda: service.search_recent(
                text=query,
                stream_id=stream_id,
                seconds=range_seconds,
                limit=limit,
                fields=fields,
                sort_field=sort,
                sort_order=sort_order,
            )
        )

    @server.tool()
    def find_logs_between(
        query: str,
        stream_id: str,
        from_time: str,
        to_time: str,
        limit: int = 50,
        fields: str = "",
        sort: str = "timestamp",
        sort_order: str = "desc",
    ) -> str:
        """Search messages between two ISO-8601 timestamps
        (e.g. '2026-03-15T00:00:00.000Z').

        Args:
            query: Lucene query.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            from_time: Window start.
            to_time: Window end.
            limit: Maximum messages returned (1-1000).
            fields: Optional comma-separated field names to return.
            sort: Field to sort on.
            sort_order: 'asc' or 'desc'.
        """
        return _respond(
            lambda: service.search_between(
                text=query,
                stream_id=stream_id,
                start=from_time,
                end=to_time,
                limit=limit,
                fields=fields,
                sort_field=sort,
                sort_order=sort_order,
            )
        )

    @server.tool()
    def read_log_message(index: str, message_id: str, fields: str = "") -> str:
        """Fetch one message in full, using the index and _id seen in search results.

        Args:
            index: Index holding the message.
            message_id: The message's _id.
            fields: Optional comma-separated field names to return.
        """
        return _respond(lambda: service.message(index=index, message_id=message_id, fields=fields))

    @server.tool()
    def rank_field_values(
        query: str,
        field: str,
        stream_id: str,
        range_seconds: int = 86400,
        top_n: int = 20,
    ) -> str:
        """Rank the most frequent values of a field among matching messages,
        e.g. which pods or error texts appear most in the last day.

        Counts are computed over the newest matches (up to 1000); the result
        says so when more messages matched. On Graylog 5.1+ prefer
        exact_field_counts, which counts every match on the server.

        Args:
            query: Lucene query selecting the messages to count.
            field: Field whose values are ranked.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (default 24 h).
            top_n: How many values to return (1-100).
        """
        return _respond(
            lambda: service.top_values(
                text=query, stream_id=stream_id, field=field, seconds=range_seconds, top=top_n
            )
        )

    @server.tool()
    def discover_fields(stream_id: str, range_seconds: int = 3600) -> str:
        """List field names present in recent messages of a stream, to learn
        what can be searched or aggregated.

        Args:
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window used for sampling (default 1 h).
        """
        return _respond(
            lambda: service.discover_fields(stream_id=stream_id, seconds=range_seconds)
        )
