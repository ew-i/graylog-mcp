"""`AlertStore` backed by Graylog's events API."""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.alerts import Event, EventDefinition, EventPage, EventQuery
from .graylog_api import GraylogApi, first_list, integer, objects, text, timerange_json


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
                "page": 1,
                "per_page": query.limit,
                "filter": filter_body,
                "timerange": timerange_json(query.window),
                "sort_by": "timestamp",
                "sort_direction": "desc",
            },
        )
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        titles = {
            def_id: (entry or {}).get("title")
            for def_id, entry in (context.get("event_definitions") or {}).items()
            if isinstance(entry, dict) or entry is None
        }
        events = []
        for item in objects(body.get("events")):
            raw = item.get("event") if isinstance(item.get("event"), dict) else item
            definition_id = text(raw.get("event_definition_id"))
            events.append(
                Event(
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
                )
            )
        total = integer(body.get("total_events"))
        return EventPage(total=total if total is not None else len(events), events=tuple(events))

    def definitions(self, text_query: str, limit: int) -> list[EventDefinition]:
        body = self._api.get_object(
            "/api/events/definitions",
            params={"page": 1, "per_page": limit, "query": text_query},
        )
        out = []
        for item in first_list(body, ("event_definitions", "elements")):
            config = item.get("config") if isinstance(item.get("config"), dict) else {}
            streams = config.get("streams") if isinstance(config.get("streams"), list) else []
            out.append(
                EventDefinition(
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
            )
        return out
