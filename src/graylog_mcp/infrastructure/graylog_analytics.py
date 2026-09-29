"""`AggregationStore` backed by Graylog's Search Scripting API (Graylog 5.1+).

The API requires at least one grouping. For an ungrouped aggregation this adapter
groups by the `streams` field and keeps the row for the queried stream: every
matching message belongs to that stream, so that row covers all matches.
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
        body = {
            "query": query.text,
            "streams": [query.stream_id],
            "timerange": timerange_json(query.window),
            "group_by": groups,
            "metrics": [_metric_json(m) for m in query.metrics],
        }
        try:
            response = self._api.post_object(AGGREGATE_PATH, body)
        except NotFoundError as exc:
            raise UnsupportedError(
                "server-side aggregation needs the Search Scripting API (Graylog 5.1 or newer)"
            ) from exc

        width = len(groups) + len(query.metrics)
        labels = [m.label for m in query.metrics]
        rows: list[AggregationRow] = []
        for raw in response.get("datarows") or []:
            if not isinstance(raw, list) or len(raw) != width:
                raise BackendError("unexpected row shape in aggregation response")
            keys = tuple(_key(v) for v in raw[: len(groups)])
            values = dict(zip(labels, raw[len(groups) :], strict=True))
            if synthetic:
                if keys[0] != query.stream_id:
                    continue
                keys = ()
            rows.append(AggregationRow(keys=keys, values=values))
        return AggregationTable(rows=tuple(rows))
