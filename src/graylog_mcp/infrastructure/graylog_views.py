"""`ViewStore` backed by Graylog's views, saved-search and dashboard APIs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from ..domain.errors import InvalidRequestError
from ..domain.models import Between, LastSeconds, TimeWindow
from ..domain.views import SavedQuery, ViewSummary
from .graylog_api import GraylogApi, first_list, integer, objects, segment, text

_LIST_KEYS = ("elements", "views", "dashboards", "saved_searches", "result")


def _summary(item: dict[str, Any]) -> ViewSummary:
    return ViewSummary(
        id=str(item.get("id", "")),
        title=text(item.get("title")),
        description=text(item.get("description")) or None,
        summary=text(item.get("summary")) or None,
        owner=text(item.get("owner")),
        last_updated=text(item.get("last_updated_at") or item.get("created_at")),
    )


def _stream_ids(node: Any) -> Iterator[str]:
    """Walk a (possibly nested) filter tree and yield stream IDs."""
    if isinstance(node, dict):
        if node.get("type") == "stream" and node.get("id"):
            yield str(node["id"])
        for value in node.values():
            yield from _stream_ids(value)
    elif isinstance(node, list):
        for item in node:
            yield from _stream_ids(item)


def _window(timerange: Any) -> tuple[TimeWindow | None, str | None]:
    if not isinstance(timerange, dict):
        return None, None
    kind = timerange.get("type")
    if kind == "relative":
        seconds = integer(timerange.get("range"))
        if seconds is None:
            seconds = integer(timerange.get("from"))  # newer format: from/to offsets in seconds
        if seconds:
            return LastSeconds(seconds), None
        return None, "all time"
    if kind == "absolute" and timerange.get("from") and timerange.get("to"):
        return Between(str(timerange["from"]), str(timerange["to"])), None
    if kind == "keyword":
        return None, text(timerange.get("keyword"))
    return None, text(kind)


class GraylogViewStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    def _listing(self, path: str, query: str, limit: int) -> list[ViewSummary]:
        params: dict[str, Any] = {"page": 1, "per_page": limit, "sort": "title", "order": "asc"}
        if query:
            params["query"] = query
        return [_summary(i) for i in first_list(self._api.get_object(path, params), _LIST_KEYS)]

    def saved_searches(self, text_query: str, limit: int) -> list[ViewSummary]:
        return self._listing("/api/search/saved", text_query, limit)

    def dashboards(self, text_query: str, limit: int) -> list[ViewSummary]:
        return self._listing("/api/dashboards", text_query, limit)

    def saved_query(self, view_id: str) -> SavedQuery:
        view = self._api.get_object(f"/api/views/{segment(view_id)}")
        search_id = view.get("search_id")
        if not search_id:
            raise InvalidRequestError(f"view {view_id} has no search attached")
        search = self._api.get_object(f"/api/views/search/{segment(str(search_id))}")
        queries = objects(search.get("queries"))
        first = queries[0] if queries else {}
        query_part = first.get("query") if isinstance(first.get("query"), dict) else {}
        window, keyword = _window(first.get("timerange"))
        streams = tuple(dict.fromkeys(_stream_ids([first.get("filter"), first.get("filters")])))
        return SavedQuery(
            view_id=view_id,
            title=text(view.get("title")),
            query_text=str(query_part.get("query_string") or ""),
            stream_ids=streams,
            window=window,
            keyword=keyword,
        )
