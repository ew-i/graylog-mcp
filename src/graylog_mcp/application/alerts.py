"""Alert and event use cases."""

from __future__ import annotations

from ..domain.alerts import EventDefinition, EventQuery, priority_label
from ..domain.models import JsonDict, LastSeconds
from .pagination import listing, open_page, page_metadata, pinned_window
from .policy import Clock, Limits, clamp, utc_now
from .ports import AlertStore


def _seconds(ms):
    return None if ms is None else round(ms / 1000)


class AlertService:
    def __init__(
        self, store: AlertStore, limits: Limits | None = None, clock: Clock = utc_now
    ) -> None:
        self._store = store
        self._limits = limits or Limits()
        self._clock = clock

    def recent_alerts(
        self,
        *,
        seconds: int,
        limit: int,
        alerts_only: bool = True,
        text: str = "",
        definition_id: str = "",
        next_cursor: str = "",
    ) -> JsonDict:
        page = open_page(
            "events",
            next_cursor,
            limit=limit,
            max_limit=self._limits.max_events,
            match={"text": text.strip(), "definition_id": definition_id.strip()},
            details=lambda: {
                "window": pinned_window(
                    LastSeconds(clamp(seconds, 1, self._limits.max_window_seconds)), self._clock()
                ),
                "alerts_only": alerts_only,
            },
        )
        definition = page.value("definition_id")
        query = EventQuery(
            window=page.window(),
            limit=page.limit,
            text=page.value("text"),
            alerts_only=page.value("alerts_only", bool),
            definition_ids=(definition,) if definition else (),
            offset=page.offset,
        )
        found = self._store.events(query)
        return {
            "window": page.request["window"],
            "alerts_only": query.alerts_only,
            "total": found.total,
            "returned": len(found.events),
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
                for e in found.events
            ],
            **page_metadata(
                page,
                len(found.events),
                found.total,
                next_offset=found.next_offset,
                reachable=self._limits.max_result_window,
            ),
        }

    def alert_definitions(
        self, *, text: str = "", limit: int = 50, next_cursor: str = ""
    ) -> JsonDict:
        found, paging = listing(
            "definitions",
            next_cursor,
            text=text,
            limit=limit,
            max_limit=self._limits.max_listing,
            fetch=self._store.definitions,
        )
        return {
            "total": found.total,
            "returned": len(found.items),
            "definitions": [self._definition(d) for d in found.items],
            **paging,
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
