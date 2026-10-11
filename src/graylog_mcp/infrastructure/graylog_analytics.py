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
from ..domain.capabilities import Capability
from ..domain.errors import BackendError, NotFoundError, UnsupportedError
from .graylog_api import GraylogApi, timerange_json
from .graylog_capabilities import AGGREGATE_PATH, NO_TIME_GROUPING, GraylogCapabilities

_STREAM_GROUP_LIMIT = 100
_NO_AGGREGATION = "server-side aggregation needs the Search Scripting API (Graylog 5.1 or newer)"
_NO_TIME_BUCKETS = (
    "this Graylog version's Search Scripting API cannot group by time "
    "(Graylog 5.1 cannot); count matches per window with count_matches instead"
)


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
    def __init__(
        self, client: httpx.Client, capabilities: GraylogCapabilities | None = None
    ) -> None:
        self._api = GraylogApi(client)
        self._capabilities = capabilities or GraylogCapabilities(client)

    def aggregate(self, query: AggregationQuery) -> AggregationTable:
        if self._capabilities.known(Capability.AGGREGATION) is False:
            raise UnsupportedError(_NO_AGGREGATION)
        timed = any(isinstance(g, TimeGrouping) for g in query.groups)
        if timed and self._capabilities.known(Capability.TIME_GROUPING) is False:
            raise UnsupportedError(_NO_TIME_BUCKETS)
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
            if self._capabilities.supports(Capability.AGGREGATION):
                raise  # the API exists, so something the query names does not
            raise UnsupportedError(_NO_AGGREGATION) from exc
        except BackendError as exc:
            if NO_TIME_GROUPING in str(exc):
                self._capabilities.record(Capability.TIME_GROUPING, False)
                raise UnsupportedError(_NO_TIME_BUCKETS) from exc
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
