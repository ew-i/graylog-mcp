"""`LogStore` implementation backed by the Graylog REST API."""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..domain.errors import NotFoundError
from ..domain.models import (
    Between,
    ClusterInfo,
    LastSeconds,
    ListingPage,
    LogEntry,
    LogQuery,
    SearchPage,
    Stream,
)
from .graylog_api import (
    GraylogApi,
    build_http_client,
    integer,
    objects,
    page_number,
    segment,
    total_count,
)

__all__ = ["GraylogStore", "build_http_client"]

# Graylog's built-in "All messages" stream. The paginated API cannot filter on
# is_default, so it is excluded by this fixed ID instead.
DEFAULT_STREAM_ID = "000000000000000000000001"
_OBJECT_ID = re.compile(r"[0-9a-fA-F]{24}")

_SEARCH_PATHS = {
    LastSeconds: "/api/search/universal/relative",
    Between: "/api/search/universal/absolute",
}


def _unwrap(hit: dict[str, Any]) -> dict[str, Any]:
    """Search hits nest fields under 'message'."""
    inner = hit.get("message")
    return inner if isinstance(inner, dict) else hit


def _unwrap_single(body: dict[str, Any]) -> dict[str, Any]:
    """Single-message lookups nest fields one level deeper, under 'message.fields'."""
    inner = _unwrap(body)
    fields = inner.get("fields")
    return fields if isinstance(fields, dict) else inner


def _streams(items: Any) -> tuple[Stream, ...]:
    return tuple(
        Stream(
            id=item["id"],
            title=item.get("title"),
            description=item.get("description"),
            disabled=bool(item.get("disabled", False)),
        )
        for item in objects(items)
        if "id" in item and not item.get("is_default")
    )


def _index(hit: dict[str, Any]) -> str | None:
    value = hit.get("index")
    return str(value) if value is not None else None


class GraylogStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    def cluster_info(self) -> ClusterInfo:
        body = self._api.get_object("/api/system")
        return ClusterInfo(
            version=body.get("version"),
            codename=body.get("codename"),
            cluster_id=body.get("cluster_id"),
            node_id=body.get("node_id"),
            hostname=body.get("hostname"),
            is_processing=body.get("is_processing"),
            timezone=body.get("timezone"),
        )

    def streams(self, offset: int, limit: int) -> ListingPage:
        try:
            body = self._api.get_object(
                "/api/streams/paginated",
                params={
                    "page": page_number(offset, limit),
                    "per_page": limit,
                    "query": f"-id:{DEFAULT_STREAM_ID}",
                    "sort": "title",
                    "order": "asc",
                },
            )
        except NotFoundError:  # servers without the paginated endpoint list everything
            every = _streams(self._api.get_object("/api/streams").get("streams"))
            page = every[offset : offset + limit]
            return ListingPage(total=len(every), items=page, next_offset=offset + len(page))
        items = _streams(body.get("elements"))
        total = total_count(body, "total")
        return ListingPage(
            total=total if total is not None else len(items),
            items=items,
            next_offset=offset + limit,
        )

    def stream_exists(self, stream_id: str) -> bool:
        if not _OBJECT_ID.fullmatch(stream_id):
            return False  # Graylog answers a malformed ID with HTTP 500, not 404
        try:
            self._api.get_object(f"/api/streams/{segment(stream_id)}")
        except NotFoundError:
            return False
        return True

    def search(self, query: LogQuery) -> SearchPage:
        body = self._api.get_object(
            _SEARCH_PATHS[type(query.window)], params=self._search_params(query)
        )
        hits = body.get("messages")
        entries = tuple(LogEntry(_unwrap(hit), index=_index(hit)) for hit in objects(hits))
        # The search API pages by offset: continue after every hit it sent, kept or not.
        sent = len(hits) if isinstance(hits, list) else 0
        return SearchPage(
            total_hits=integer(body.get("total_results")) or 0,
            entries=entries,
            next_offset=query.offset + sent,
        )

    def fetch(self, index: str, message_id: str) -> LogEntry:
        return LogEntry(
            _unwrap_single(
                self._api.get_object(f"/api/messages/{segment(index)}/{segment(message_id)}")
            ),
            index=index,
        )

    @staticmethod
    def _search_params(query: LogQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": query.text,
            "limit": query.max_results,
            "sort": query.sort.as_param(),
            "filter": f"streams:{query.stream_id}",
        }
        if query.offset:
            params["offset"] = query.offset
        window = query.window
        if isinstance(window, LastSeconds):
            params["range"] = window.seconds
        else:
            params["from"] = window.start
            params["to"] = window.end
        if query.only_fields:
            params["fields"] = ",".join(query.only_fields)
        return params
