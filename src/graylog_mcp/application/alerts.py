"""Alert and event use cases."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Any

from ..domain.alerts import Event, EventDefinition, EventQuery, priority_label
from ..domain.errors import BackendError, InvalidRequestError, error_payload
from ..domain.models import Between, JsonDict
from ..domain.timeutil import format_timestamp, parse_timestamp
from .pagination import listing, open_page, page_metadata, pinned_window
from .policy import Clock, Limits, clamp, utc_now
from .ports import AlertStore
from .service import LogService

_EVENT_ID = re.compile(r"[0-9A-Za-z]+")  # Graylog event IDs are ULIDs


def _seconds(ms):
    return None if ms is None else round(ms / 1000)


def _event_id(raw: str) -> str:
    event_id = raw.strip()
    if not _EVENT_ID.fullmatch(event_id):
        raise InvalidRequestError("event_id must be an event ID from recent_alerts")
    return event_id


def _iso(raw: str) -> str:
    return format_timestamp(parse_timestamp(raw))


def _quoted(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _matching_query(base: str | None, group_by: Mapping[str, Any]) -> str:
    """The definition query narrowed to the event's group-by values."""
    terms = [f"{name}:{_quoted(value)}" for name, value in group_by.items()]
    base = (base or "").strip()
    if base and base != "*":
        terms.insert(0, f"({base})")
    return " AND ".join(terms) or "*"


def _matching_window(event: Event, definition: EventDefinition | None) -> Between | None:
    """Aggregation events carry their window; a filter event ends at its message."""
    if event.timerange_start and event.timerange_end:
        return Between(_iso(event.timerange_start), _iso(event.timerange_end))
    if event.timestamp and definition and definition.search_within_ms:
        end = parse_timestamp(event.timestamp) + timedelta(milliseconds=1)
        start = end - timedelta(milliseconds=definition.search_within_ms + 1)
        return Between(format_timestamp(start), format_timestamp(end))
    return None


class AlertService:
    def __init__(
        self,
        store: AlertStore,
        search: LogService,
        limits: Limits | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._store = store
        self._search = search
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
                "window": pinned_window(self._limits.recent_window(seconds), self._clock()),
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
            "events": [self._event(e) for e in found.events],
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

    def alert_details(self, *, event_id: str, limit: int = 20) -> JsonDict:
        event = self._store.event(_event_id(event_id))
        result: JsonDict = {}
        definition = None
        if event.definition_id:
            try:
                definition = self._store.definition(event.definition_id)
                result["definition"] = self._definition(definition)
                event = replace(event, definition_title=event.definition_title or definition.title)
            except BackendError as exc:
                result["definition"] = {"id": event.definition_id, **error_payload(exc)}
        result = {"event": self._event(event), **result}

        origin = event.origin_message()
        if origin:
            try:
                result["originating_message"] = self._search.message(
                    index=origin["_index"], message_id=origin["_id"]
                )
            except BackendError as exc:
                result["originating_message"] = {**origin, **error_payload(exc)}

        # Without the definition's query, a search would return every message, not the matches.
        streams = (event.source_streams or definition.stream_ids) if definition else ()
        window = _matching_window(event, definition)
        if not streams or window is None:
            result["matching_messages"] = []
            result["note"] = (
                "the definition, streams or time window is unknown, so no messages were "
                "searched; use find_logs_between around the event time"
            )
            return result
        query = _matching_query(definition.query, event.group_by_fields)
        matching = []
        for stream_id in streams:
            try:
                matching.append(
                    self._search.search_between(
                        text=query,
                        stream_id=stream_id,
                        start=window.start,
                        end=window.end,
                        limit=limit,
                    )
                )
            except BackendError as exc:
                matching.append({"stream_id": stream_id, **error_payload(exc)})
        result["matching_messages"] = matching
        return result

    def find_related_alerts(
        self, *, event_id: str, seconds: int = 3600, limit: int = 50
    ) -> JsonDict:
        event = self._store.event(_event_id(event_id))
        if not event.timestamp:
            raise InvalidRequestError(f"event {event.id!r} has no timestamp")
        center = parse_timestamp(event.timestamp)
        span = timedelta(seconds=clamp(seconds, 1, self._limits.max_window_seconds))
        window = Between(format_timestamp(center - span), format_timestamp(center + span))
        found = self._store.events(
            EventQuery(window=window, limit=self._limits.max_events, alerts_only=False)
        )
        related = []
        for other in found.events:
            if other.id == event.id:
                continue
            same_definition = (
                bool(event.definition_id) and other.definition_id == event.definition_id
            )
            reasons = ["same definition"] if same_definition else []
            reasons += [
                f"same {name}"
                for name, value in event.group_by_fields.items()
                if other.group_by_fields.get(name) == value
            ]
            if reasons:
                related.append({**self._event(other), "related_by": reasons})
        shown = related[: clamp(limit, 1, self._limits.max_events)]
        result: JsonDict = {
            "event": self._event(event),
            "window": window.describe(),
            "total": len(related),
            "returned": len(shown),
            "related": shown,
        }
        if found.total > len(found.events):
            result["note"] = (
                f"only the newest {len(found.events)} of {found.total} events in the window "
                "were compared; narrow range_seconds to cover the rest"
            )
        return result

    @staticmethod
    def _event(e: Event) -> JsonDict:
        return {
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
            "origin_message": e.origin_message(),
            "timerange": (
                {"from": e.timerange_start, "to": e.timerange_end}
                if e.timerange_start or e.timerange_end
                else None
            ),
            "source_streams": list(e.source_streams),
            "group_by_fields": dict(e.group_by_fields),
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
