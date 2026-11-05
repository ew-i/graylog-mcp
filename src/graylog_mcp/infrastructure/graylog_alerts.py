"""`AlertStore` backed by Graylog's events API."""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.alerts import Event, EventDefinition, EventPage, EventQuery
from ..domain.errors import NotFoundError
from ..domain.models import ListingPage, ListingQuery
from .graylog_api import (
    GraylogApi,
    first_list,
    integer,
    objects,
    page_number,
    segment,
    text,
    timerange_json,
    total_count,
)


class GraylogAlertStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    def events(self, query: EventQuery) -> EventPage:
        filter_body: dict[str, Any] = {"alerts": "only" if query.alerts_only else "include"}
        if query.definition_ids:
            filter_body["event_definitions"] = list(query.definition_ids)
        body = self._api.post_object(
            "/api/events/search",
            {
                "query": query.text,
                "page": page_number(query.offset, query.limit),
                "per_page": query.limit,
                "filter": filter_body,
                "timerange": timerange_json(query.window),
                "sort_by": "timestamp",
                "sort_direction": "desc",
            },
        )
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        definitions = context.get("event_definitions")
        titles = {
            def_id: (entry or {}).get("title")
            for def_id, entry in (definitions if isinstance(definitions, dict) else {}).items()
            if isinstance(entry, dict) or entry is None
        }
        events = [_event(item, titles) for item in objects(body.get("events"))]
        total = integer(body.get("total_events"))
        return EventPage(
            total=total if total is not None else len(events),
            events=tuple(events),
            next_offset=query.offset + query.limit,
        )

    def event(self, event_id: str) -> Event:
        try:
            found = self._api.get(f"/api/events/{segment(event_id)}")
        except NotFoundError:
            # Graylog before 5.1 has no lookup by ID (a 404 there says nothing), so search for it.
            found = None
        if not isinstance(found, dict) or not found:
            body = self._api.post_object(
                "/api/events/search",
                {
                    "query": f'id:"{event_id}"',
                    "page": 1,
                    "per_page": 1,
                    "filter": {"alerts": "include"},
                    "timerange": {"type": "relative", "range": 0},  # 0 is all time
                },
            )
            found = next(iter(objects(body.get("events"))), None)
        if not found:
            raise NotFoundError(
                f"event {event_id!r} does not exist or this token cannot read it; "
                "call recent_alerts to find one"
            )
        return _event(found, {})

    def definition(self, definition_id: str) -> EventDefinition:
        return _definition(
            self._api.get_object(f"/api/events/definitions/{segment(definition_id)}")
        )

    def definitions(self, query: ListingQuery) -> ListingPage:
        body = self._api.get_object(
            "/api/events/definitions",
            params={
                "page": page_number(query.offset, query.limit),
                "per_page": query.limit,
                "query": query.text,
            },
        )
        out = [_definition(item) for item in first_list(body, ("event_definitions", "elements"))]
        total = total_count(body, "total", "total_results", "count")
        return ListingPage(
            total=total if total is not None else len(out),
            items=tuple(out),
            next_offset=query.offset + query.limit,
        )


def _event(item: dict[str, Any], titles: dict[str | None, Any]) -> Event:
    raw = item.get("event") if isinstance(item.get("event"), dict) else item
    definition_id = text(raw.get("event_definition_id"))
    streams = raw.get("source_streams") if isinstance(raw.get("source_streams"), list) else []
    group_by = raw.get("group_by_fields")
    return Event(
        id=str(raw.get("id", "")),
        definition_id=definition_id,
        definition_title=titles.get(definition_id),
        timestamp=text(raw.get("timestamp")),
        message=text(raw.get("message")),
        priority=integer(raw.get("priority")),
        alert=bool(raw.get("alert", False)),
        source=text(raw.get("source")),
        key=text(raw.get("key")),
        fields=raw.get("fields") if isinstance(raw.get("fields"), dict) else {},
        origin_context=text(raw.get("origin_context")),
        timerange_start=text(raw.get("timerange_start")),
        timerange_end=text(raw.get("timerange_end")),
        source_streams=tuple(str(s) for s in streams),
        group_by_fields=group_by if isinstance(group_by, dict) else {},
    )


def _definition(item: dict[str, Any]) -> EventDefinition:
    config = item.get("config") if isinstance(item.get("config"), dict) else {}
    streams = config.get("streams") if isinstance(config.get("streams"), list) else []
    return EventDefinition(
        id=str(item.get("id", "")),
        title=text(item.get("title")),
        description=text(item.get("description")) or None,
        priority=integer(item.get("priority")),
        alert=bool(item.get("alert", False)),
        kind=text(config.get("type")),
        query=text(config.get("query")),
        stream_ids=tuple(str(s) for s in streams),
        search_within_ms=integer(config.get("search_within_ms")),
        execute_every_ms=integer(config.get("execute_every_ms")),
    )
