"""Use cases exposed to any front end (MCP today, maybe a CLI tomorrow).

This layer owns *policy*: defaults, upper bounds, and how raw backend
results are shaped for a caller. It knows nothing about HTTP or MCP.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..domain.analysis import field_names, rank_values
from ..domain.errors import InvalidRequestError
from ..domain.models import (
    Between,
    JsonDict,
    LastSeconds,
    LogQuery,
    Sort,
    SortDirection,
    TimeWindow,
)
from .pagination import open_page, page_metadata, pinned_window
from .policy import Clock, Limits, parse_field_list, utc_now
from .policy import clamp as _clamp
from .ports import LogStore

__all__ = ["Limits", "LogService", "parse_field_list"]


class LogService:
    def __init__(
        self, store: LogStore, limits: Limits | None = None, clock: Clock = utc_now
    ) -> None:
        self._store = store
        self._limits = limits or Limits()
        self._clock = clock

    # -- cluster / catalogue -------------------------------------------------

    def cluster_info(self) -> JsonDict:
        return asdict(self._store.cluster_info())

    def streams(self, *, limit: int = 50, next_cursor: str = "") -> JsonDict:
        page = open_page("streams", next_cursor, limit=limit, max_limit=self._limits.max_listing)
        visible = [
            {"id": s.id, "title": s.title, "description": s.description, "disabled": s.disabled}
            for s in self._store.streams()
            if not s.is_default
        ]
        items = visible[page.offset : page.offset + page.limit]
        # ponytail: slice locally until Graylog's streams endpoint exposes pages.
        return {
            "total": len(visible),
            "returned": len(items),
            "streams": items,
            **page_metadata(page, len(items), len(visible), next_offset=page.offset + len(items)),
        }

    # -- search --------------------------------------------------------------

    def search_recent(
        self,
        *,
        text: str,
        stream_id: str,
        seconds: int,
        limit: int,
        fields: str = "",
        sort_field: str = "timestamp",
        sort_order: str = "desc",
        next_cursor: str = "",
    ) -> JsonDict:
        window = LastSeconds(_clamp(seconds, 1, self._limits.max_window_seconds))
        return self._search(
            window, text, stream_id, limit, fields, sort_field, sort_order, next_cursor
        )

    def search_between(
        self,
        *,
        text: str,
        stream_id: str,
        start: str,
        end: str,
        limit: int,
        fields: str = "",
        sort_field: str = "timestamp",
        sort_order: str = "desc",
        next_cursor: str = "",
    ) -> JsonDict:
        return self._search(
            Between(start, end),
            text,
            stream_id,
            limit,
            fields,
            sort_field,
            sort_order,
            next_cursor,
        )

    def _search(
        self,
        window: TimeWindow,
        text: str,
        stream_id: str,
        limit: int,
        fields: str,
        sort_field: str,
        sort_order: str,
        next_cursor: str,
    ) -> JsonDict:
        page = open_page(
            "search",
            next_cursor,
            limit=limit,
            max_limit=self._limits.max_results,
            match={"text": text.strip(), "stream_id": stream_id.strip()},
            details=lambda: {
                "window": pinned_window(window, self._clock()),
                "fields": list(parse_field_list(fields)),
                "sort_field": sort_field.strip(),
                "sort_order": SortDirection.parse(sort_order).value,
            },
        )
        query = LogQuery(
            text=page.value("text"),
            stream_id=page.value("stream_id"),
            window=page.window(),
            max_results=page.limit,
            offset=page.offset,
            sort=Sort(page.value("sort_field"), SortDirection.parse(page.value("sort_order"))),
            only_fields=parse_field_list(",".join(map(str, page.value("fields", list)))),
        )
        found = self._store.search(query)
        messages = [entry.listed(query.only_fields) for entry in found.entries]
        return {
            "query": query.text,
            "stream_id": query.stream_id,
            "window": page.request["window"],
            "total_results": found.total_hits,
            "returned": len(messages),
            "messages": messages,
            **page_metadata(
                page,
                len(messages),
                found.total_hits,
                next_offset=found.next_offset,
                reachable=self._limits.max_result_window,
            ),
        }

    # -- single message ------------------------------------------------------

    def message(self, *, index: str, message_id: str, fields: str = "") -> JsonDict:
        if not index.strip() or not message_id.strip():
            raise InvalidRequestError("both index and message_id are required")
        entry = self._store.fetch(index.strip(), message_id.strip())
        return entry.visible(parse_field_list(fields))

    # -- analysis ------------------------------------------------------------

    def top_values(
        self, *, text: str, stream_id: str, field: str, seconds: int, top: int
    ) -> JsonDict:
        field = field.strip()
        if not field:
            raise InvalidRequestError("field must not be blank")

        window = LastSeconds(_clamp(seconds, 1, self._limits.max_window_seconds))
        sample = self._limits.aggregation_sample
        page = self._store.search(
            LogQuery(
                text=text,
                stream_id=stream_id,
                window=window,
                max_results=sample,
                only_fields=("timestamp", field),
            )
        )
        ranked = rank_values(page.entries, field, _clamp(top, 1, self._limits.max_top_values))
        result: dict[str, Any] = {
            "query": text,
            "field": field,
            "window": window.describe(),
            "total_matching_messages": page.total_hits,
            "sampled_messages": len(page.entries),
            "top_values": [asdict(v) for v in ranked],
        }
        if page.total_hits > len(page.entries):
            result["note"] = (
                f"Counts come from the {len(page.entries)} newest matches only; "
                f"{page.total_hits} messages matched in total."
            )
        return result

    def discover_fields(self, *, stream_id: str, seconds: int) -> JsonDict:
        window = LastSeconds(_clamp(seconds, 1, self._limits.max_window_seconds))
        page = self._store.search(
            LogQuery(
                text="*",
                stream_id=stream_id,
                window=window,
                max_results=self._limits.field_discovery_sample,
            )
        )
        names = field_names(page.entries)
        return {
            "stream_id": stream_id,
            "window": window.describe(),
            "sampled_messages": len(page.entries),
            "count": len(names),
            "fields": names,
        }
