"""Cluster health use cases."""

from __future__ import annotations

from dataclasses import asdict

from ..domain.models import JsonDict
from .ports import SystemStore

_SEVERITY_ORDER = {"urgent": 0, "normal": 1}


class OperationsService:
    def __init__(self, store: SystemStore) -> None:
        self._store = store

    def input_status(self, *, only_problems: bool = False) -> JsonDict:
        inputs = self._store.inputs()
        rows = [
            {
                "id": i.id,
                "title": i.title,
                "type": i.type,
                "global": i.is_global,
                "node_id": i.node_id,
                "health": i.health,
                "states": [asdict(s) for s in i.states],
            }
            for i in inputs
        ]
        problems = [r for r in rows if r["health"] == "problem"]
        return {
            "total": len(rows),
            "problems": len(problems),
            "inputs": problems if only_problems else rows,
        }

    def throughput(self) -> JsonDict:
        nodes = self._store.throughput()
        totals: dict[str, float] = {}
        for node in nodes:
            for name in ("input_per_second", "output_per_second"):
                value = node.metrics.get(name)
                if value is not None:
                    totals[name] = round(totals.get(name, 0.0) + float(value), 3)
        return {
            "cluster": totals,
            "nodes": [{"node_id": n.node_id, **dict(n.metrics)} for n in nodes],
        }

    def notifications(self) -> JsonDict:
        # Two stable sorts: newest first, then urgent before normal.
        items = sorted(self._store.notifications(), key=lambda n: n.timestamp or "", reverse=True)
        items.sort(key=lambda n: _SEVERITY_ORDER.get((n.severity or "").lower(), 2))
        return {"total": len(items), "notifications": [asdict(n) for n in items]}

    def cluster_nodes(self) -> JsonDict:
        nodes = self._store.nodes()
        rows = [{**asdict(n), "healthy": n.healthy} for n in nodes]
        return {
            "total": len(rows),
            "healthy": sum(1 for r in rows if r["healthy"]),
            "leader": next((r["node_id"] for r in rows if r["is_leader"]), None),
            "nodes": rows,
        }
