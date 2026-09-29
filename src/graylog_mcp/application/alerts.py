"""Alert and event use cases."""

from __future__ import annotations

from ..domain.alerts import EventDefinition, EventQuery, priority_label
from ..domain.models import JsonDict, LastSeconds
from .policy import Limits, clamp
from .ports import AlertStore


def _seconds(ms):
    return None if ms is None else round(ms / 1000)


class AlertService:
    def __init__(self, store: AlertStore, limits: Limits | None = None) -> None:
        self._store = store
        self._limits = limits or Limits()

    def recent_alerts(
        self,
        *,
        seconds: int,
        limit: int,
        alerts_only: bool = True,
        text: str = "",
        definition_id: str = "",
    ) -> JsonDict:
        window = LastSeconds(clamp(seconds, 1, self._limits.max_window_seconds))
        query = EventQuery(
            window=window,
            limit=clamp(limit, 1, self._limits.max_events),
            text=text.strip(),
            alerts_only=alerts_only,
            definition_ids=(definition_id.strip(),) if definition_id.strip() else (),
        )
        page = self._store.events(query)
        return {
            "window": window.describe(),
            "alerts_only": alerts_only,
            "total": page.total,
            "returned": len(page.events),
            "events": [
                {
                    "id": e.id,
                    "time": e.timestamp,
                    "title": e.definition_title,
                    "message": e.message,
                    "priority": priority_label(e.priority),
                    "alert": e.alert,
                    "source": e.source,
                    "key": e.key or None,
                    "definition_id": e.definition_id,
                    "fields": dict(e.fields),
                }
                for e in page.events
            ],
        }

    def alert_definitions(self, *, text: str = "", limit: int = 50) -> JsonDict:
        definitions = self._store.definitions(
            text.strip(), clamp(limit, 1, self._limits.max_listing)
        )
        return {
            "total": len(definitions),
            "definitions": [self._definition(d) for d in definitions],
        }

    @staticmethod
    def _definition(d: EventDefinition) -> JsonDict:
        return {
            "id": d.id,
            "title": d.title,
            "description": d.description,
            "priority": priority_label(d.priority),
            "alert": d.alert,
            "type": d.kind,
            "query": d.query,
            "stream_ids": list(d.stream_ids),
            "search_within_seconds": _seconds(d.search_within_ms),
            "execute_every_seconds": _seconds(d.execute_every_ms),
        }
