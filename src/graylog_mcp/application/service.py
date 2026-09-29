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
from .policy import Limits, parse_field_list
from .policy import clamp as _clamp
from .ports import LogStore

__all__ = ["Limits", "LogService", "parse_field_list"]


class LogService:
    def __init__(self, store: LogStore, limits: Limits | None = None) -> None:
        self._store = store
        self._limits = limits or Limits()

    # -- cluster / catalogue -------------------------------------------------

    def cluster_info(self) -> JsonDict:
        return asdict(self._store.cluster_info())

    def streams(self) -> JsonDict:
        visible = [
            {"id": s.id, "title": s.title, "description": s.description, "disabled": s.disabled}
            for s in self._store.streams()
            if not s.is_default
        ]
        return {"total": len(visible), "streams": visible}

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
    ) -> JsonDict:
        window = LastSeconds(_clamp(seconds, 1, self._limits.max_window_seconds))
        return self._search(text, stream_id, window, limit, fields, sort_field, sort_order)

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
    ) -> JsonDict:
        return self._search(
            text, stream_id, Between(start, end), limit, fields, sort_field, sort_order
        )

    def _search(
        self,
        text: str,
        stream_id: str,
        window: TimeWindow,
        limit: int,
        fields: str,
        sort_field: str,
        sort_order: str,
    ) -> JsonDict:
        query = LogQuery(
            text=text,
            stream_id=stream_id,
            window=window,
            max_results=_clamp(limit, 1, self._limits.max_results),
            sort=Sort(sort_field, SortDirection.parse(sort_order)),
            only_fields=parse_field_list(fields),
        )
        page = self._store.search(query)
        messages = [entry.visible(query.only_fields) for entry in page.entries]
        return {
            "query": query.text,
            "window": window.describe(),
            "total_results": page.total_hits,
            "returned": len(messages),
            "messages": messages,
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
