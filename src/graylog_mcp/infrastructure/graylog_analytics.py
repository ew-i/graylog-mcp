"""`AggregationStore` backed by Graylog's Search Scripting API (Graylog 5.1+).

The API requires at least one grouping. For an ungrouped aggregation this adapter
groups by the `streams` field and keeps the queried stream's row. Every matching
message belongs to that stream, so its row has the highest document count; any
other stream with the same count holds the same messages. Rows are picked by that
count because Graylog labels them with stream IDs on some versions (5.1) and
stream titles on others (6.3).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.aggregation import (
    AggregationQuery,
    AggregationRow,
    AggregationTable,
    Metric,
    TimeGrouping,
    ValuesGrouping,
)
from ..domain.errors import BackendError, NotFoundError, UnsupportedError
from .graylog_api import GraylogApi, timerange_json

AGGREGATE_PATH = "/api/search/aggregate"
_STREAM_GROUP_LIMIT = 100
# How a Search Scripting API without time grouping (e.g. Graylog 5.1) rejects it.
_NO_TIME_GROUPING = "Unable to map property timeunit"


def _grouping_json(group) -> dict[str, Any]:
    if isinstance(group, TimeGrouping):
        return {"field": group.field, "timeunit": group.interval}
    if isinstance(group, ValuesGrouping):
        return {"field": group.field, "limit": group.limit}
    raise TypeError(group)


def _metric_json(metric: Metric) -> dict[str, Any]:
    body: dict[str, Any] = {"function": metric.function}
    if metric.field:
        body["field"] = metric.field
    if metric.descending:
        body["sort"] = "desc"
    if metric.function == "percentile":
        body["configuration"] = {"percentile": metric.percentile}
    return body


def _key(value: Any) -> str | None:
    return None if value is None else str(value)


class GraylogAnalyticsStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    def aggregate(self, query: AggregationQuery) -> AggregationTable:
        synthetic = not query.groups
        groups = (
            [{"field": "streams", "limit": _STREAM_GROUP_LIMIT}]
            if synthetic
            else [_grouping_json(g) for g in query.groups]
        )
        metrics = [_metric_json(m) for m in query.metrics]
        doc_count = next(  # index of a plain document count, added when missing
            (i for i, m in enumerate(query.metrics) if m.function == "count" and not m.field),
            None,
        )
        if synthetic and doc_count is None:
            doc_count = len(metrics)
            metrics.append({"function": "count"})
        body = {
            "query": query.text,
            "streams": [query.stream_id],
            "timerange": timerange_json(query.window),
            "group_by": groups,
            "metrics": metrics,
        }
        try:
            response = self._api.post_object(AGGREGATE_PATH, body)
        except NotFoundError as exc:
            raise UnsupportedError(
                "server-side aggregation needs the Search Scripting API (Graylog 5.1 or newer)"
            ) from exc
        except BackendError as exc:
            if _NO_TIME_GROUPING in str(exc):
                raise UnsupportedError(
                    "this Graylog version's Search Scripting API cannot group by time "
                    "(Graylog 5.1 cannot); count matches per window with count_matches instead"
                ) from exc
            raise

        width = len(groups) + len(metrics)
        labels = [m.label for m in query.metrics]
        rows: list[tuple[float, AggregationRow]] = []
        for raw in response.get("datarows") or []:
            if not isinstance(raw, list) or len(raw) != width:
                raise BackendError("unexpected row shape in aggregation response")
            keys = tuple(_key(v) for v in raw[: len(groups)])
            cells = raw[len(groups) :]
            values = dict(zip(labels, cells[: len(labels)], strict=True))
            if synthetic:
                count = cells[doc_count]
                rows.append(
                    (count if isinstance(count, (int, float)) else 0, AggregationRow((), values))
                )
            else:
                rows.append((0, AggregationRow(keys=keys, values=values)))
        if synthetic and rows:
            return AggregationTable(rows=(max(rows, key=lambda pair: pair[0])[1],))
        return AggregationTable(rows=tuple(row for _, row in rows))
