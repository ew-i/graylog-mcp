"""Analysis tools: counts, histograms, comparisons, statistics, surrounding messages."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.analytics import AnalyticsService
from .responses import respond


def register(server: MCPServer, service: AnalyticsService) -> None:
    @server.tool()
    def count_matches(query: str, stream_id: str, range_seconds: int = 3600) -> str:
        """Count messages matching a query without returning them. Cheap first check
        for "is this happening, and how often?".

        Args:
            query: Lucene query.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (default 1 h, max 30 days).
        """
        return respond(
            lambda: service.count_matches(text=query, stream_id=stream_id, seconds=range_seconds)
        )

    @server.tool()
    def count_over_time(
        query: str,
        stream_id: str,
        range_seconds: int = 86400,
        interval: str = "",
        split_by: str = "",
        split_limit: int = 5,
    ) -> str:
        """Message counts per time bucket (a histogram), optionally split by a field.
        Shows when something started, peaked or stopped. Uses the Search Scripting
        API (Graylog 5.1+).

        Args:
            query: Lucene query.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (default 24 h).
            interval: Bucket size like 1m, 5m, 1h, 1d. Empty picks one automatically.
            split_by: Optional field; each bucket is broken down by its top values.
            split_limit: How many values of split_by to keep per bucket (1-20).
        """
        return respond(
            lambda: service.count_over_time(
                text=query,
                stream_id=stream_id,
                seconds=range_seconds,
                interval=interval,
                split_by=split_by,
                split_limit=split_limit,
            )
        )

    @server.tool()
    def exact_field_counts(
        query: str, field: str, stream_id: str, range_seconds: int = 86400, top_n: int = 20
    ) -> str:
        """Exact top values of a field, counted by Graylog over every matching message
        (unlike rank_field_values, which samples). Uses the Search Scripting API
        (Graylog 5.1+).

        Args:
            query: Lucene query selecting the messages to count.
            field: Field whose values are counted.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (default 24 h).
            top_n: How many values to return (1-100).
        """
        return respond(
            lambda: service.exact_field_counts(
                text=query, stream_id=stream_id, field=field, seconds=range_seconds, top=top_n
            )
        )

    @server.tool()
    def compare_windows(
        query: str,
        stream_id: str,
        window_seconds: int = 3600,
        baseline_offset_seconds: int = 86400,
        field: str = "",
        top_n: int = 10,
    ) -> str:
        """Compare the most recent window with an earlier one of the same length
        (default: last hour vs. the same hour yesterday). Reports total change and,
        with `field`, which values rose, fell, appeared or disappeared. Per-value
        comparison uses the Search Scripting API (Graylog 5.1+).

        Args:
            query: Lucene query.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            window_seconds: Length of each window (min 60).
            baseline_offset_seconds: How far back the baseline window ends;
                must be >= window_seconds.
            field: Optional field to break the comparison down by.
            top_n: How many of the biggest changes to return.
        """
        return respond(
            lambda: service.compare_windows(
                text=query,
                stream_id=stream_id,
                window_seconds=window_seconds,
                baseline_offset_seconds=baseline_offset_seconds,
                field=field,
                top=top_n,
            )
        )

    @server.tool()
    def field_stats(
        query: str,
        field: str,
        stream_id: str,
        range_seconds: int = 3600,
        percentiles: str = "50,90,99",
        group_by: str = "",
        group_limit: int = 10,
    ) -> str:
        """Count, average, min, max, sum and percentiles of a numeric field such as a
        response time, optionally per value of another field. Uses the Search
        Scripting API (Graylog 5.1+).

        Args:
            query: Lucene query.
            field: Numeric field to summarise.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            range_seconds: Look-back window (default 1 h).
            percentiles: Comma-separated percentiles, e.g. '50,95,99.9'. Empty for none.
            group_by: Optional field; statistics are computed per value.
            group_limit: How many groups to return (1-100).
        """
        return respond(
            lambda: service.field_stats(
                text=query,
                stream_id=stream_id,
                field=field,
                seconds=range_seconds,
                percentiles=percentiles,
                group_by=group_by,
                group_limit=group_limit,
            )
        )

    @server.tool()
    def message_context(
        index: str,
        message_id: str,
        stream_id: str,
        before: int = 5,
        after: int = 5,
        context_field: str = "source",
        window_seconds: int = 300,
        fields: str = "",
    ) -> str:
        """The messages logged just before and after one message, like `grep -C`.
        Neighbours share the anchor's value of `context_field` (e.g. the same source
        or pod); leave it empty to use the whole stream.

        Args:
            index: Index of the anchor message (from search results).
            message_id: The anchor message's _id.
            stream_id: Required. Access is enforced per stream; get IDs from browse_streams.
            before: Messages to show before the anchor (0-50).
            after: Messages to show after the anchor (0-50).
            context_field: Field neighbours must share with the anchor,
                e.g. source or kubernetes_pod_name.
            window_seconds: How far to look on each side (max 24 h).
            fields: Optional comma-separated field names to return.
        """
        return respond(
            lambda: service.message_context(
                index=index,
                message_id=message_id,
                stream_id=stream_id,
                before=before,
                after=after,
                context_field=context_field,
                window_seconds=window_seconds,
                fields=fields,
            )
        )
