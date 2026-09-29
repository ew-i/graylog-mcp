"""`SystemStore` backed by Graylog's system and cluster APIs.

Cluster-wide endpoints are preferred; when they are missing (single-node or
older setups) the node-local variant is used and reported as node "local".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from ..domain.errors import NotFoundError
from ..domain.operations import (
    InputNodeState,
    InputStatus,
    NodeStatus,
    NodeThroughput,
    SystemNotification,
)
from .graylog_api import GraylogApi, number, objects, text

T = TypeVar("T")

THROUGHPUT_METRICS = {
    "org.graylog2.throughput.input.1-sec-rate": "input_per_second",
    "org.graylog2.throughput.output.1-sec-rate": "output_per_second",
    "org.graylog2.buffers.input.usage": "input_buffer_usage",
    "org.graylog2.buffers.process.usage": "process_buffer_usage",
    "org.graylog2.buffers.output.usage": "output_buffer_usage",
    "org.graylog2.journal.entries-uncommitted": "journal_uncommitted_entries",
}


def _with_fallback(primary: Callable[[], T], fallback: Callable[[], T]) -> T:
    try:
        return primary()
    except NotFoundError:
        return fallback()


def _metric_value(entry: dict[str, Any]) -> Any:
    metric = entry.get("metric")
    if isinstance(metric, dict):
        for key in ("value", "count", "rate"):
            if key in metric:
                value = metric[key]
                return value.get("one_minute") if isinstance(value, dict) else value
    return metric


class GraylogSystemStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    # -- inputs --------------------------------------------------------------

    def inputs(self) -> list[InputStatus]:
        definitions = objects(self._api.get_object("/api/system/inputs").get("inputs"))
        states_by_node = _with_fallback(
            lambda: {
                node: objects(states)
                for node, states in self._api.get_object("/api/cluster/inputstates").items()
            },
            lambda: {
                "local": objects(self._api.get_object("/api/system/inputstates").get("states"))
            },
        )
        states: dict[str, list[InputNodeState]] = {}
        for node, entries in states_by_node.items():
            for entry in entries:
                nested = (
                    entry.get("message_input")
                    if isinstance(entry.get("message_input"), dict)
                    else {}
                )
                input_id = entry.get("id") or nested.get("id")
                if not input_id:
                    continue
                states.setdefault(str(input_id), []).append(
                    InputNodeState(
                        node_id=str(node),
                        state=str(entry.get("state", "UNKNOWN")),
                        started_at=text(entry.get("started_at")),
                        message=text(entry.get("detailed_message")),
                    )
                )
        return [
            InputStatus(
                id=str(d.get("id")),
                title=text(d.get("title")),
                type=text(d.get("name") or d.get("type")),
                is_global=bool(d.get("global", False)),
                node_id=text(d.get("node")),
                states=tuple(states.get(str(d.get("id")), [])),
            )
            for d in definitions
            if d.get("id")
        ]

    # -- throughput ----------------------------------------------------------

    def throughput(self) -> list[NodeThroughput]:
        request = {"metrics": list(THROUGHPUT_METRICS)}

        def cluster() -> dict[str, Any]:
            return self._api.post_object("/api/cluster/metrics/multiple", request)

        def local() -> dict[str, Any]:
            return {"local": self._api.post_object("/api/system/metrics/multiple", request)}

        per_node = _with_fallback(cluster, local)
        out = []
        for node, summary in per_node.items():
            entries = objects(summary.get("metrics")) if isinstance(summary, dict) else []
            by_name = {e.get("full_name") or e.get("name"): e for e in entries}
            out.append(
                NodeThroughput(
                    node_id=str(node),
                    metrics={
                        friendly: number(_metric_value(by_name[full])) if full in by_name else None
                        for full, friendly in THROUGHPUT_METRICS.items()
                    },
                )
            )
        return out

    # -- notifications -------------------------------------------------------

    def notifications(self) -> list[SystemNotification]:
        body = self._api.get_object("/api/system/notifications")
        return [
            SystemNotification(
                type=text(n.get("type")),
                severity=text(n.get("severity")),
                timestamp=text(n.get("timestamp")),
                node_id=text(n.get("node_id")),
                title=text(n.get("title")),
                description=text(n.get("description")),
                details=n.get("details") if isinstance(n.get("details"), dict) else {},
            )
            for n in objects(body.get("notifications"))
        ]

    # -- nodes ---------------------------------------------------------------

    def nodes(self) -> list[NodeStatus]:
        overviews = self._api.get_object("/api/cluster")
        registry = {
            str(n.get("node_id")): n
            for n in objects(self._api.get_object("/api/system/cluster/nodes").get("nodes"))
        }
        out = []
        for node_id in dict.fromkeys([*overviews, *registry]):
            overview = overviews.get(node_id)
            entry = registry.get(node_id, {})
            leader = entry.get("is_leader", entry.get("is_master"))
            if isinstance(overview, dict):
                out.append(
                    NodeStatus(
                        node_id=node_id,
                        reachable=True,
                        hostname=text(overview.get("hostname") or entry.get("hostname")),
                        version=text(overview.get("version")),
                        is_leader=leader if isinstance(leader, bool) else None,
                        is_processing=overview.get("is_processing"),
                        lifecycle=text(overview.get("lifecycle")),
                        lb_status=text(overview.get("lb_status")),
                        started_at=text(overview.get("started_at")),
                        last_seen=text(entry.get("last_seen")),
                        transport_address=text(entry.get("transport_address")),
                    )
                )
            else:
                out.append(
                    NodeStatus(
                        node_id=node_id,
                        reachable=False,
                        hostname=text(entry.get("hostname")),
                        is_leader=leader if isinstance(leader, bool) else None,
                        last_seen=text(entry.get("last_seen")),
                        transport_address=text(entry.get("transport_address")),
                    )
                )
        return out
