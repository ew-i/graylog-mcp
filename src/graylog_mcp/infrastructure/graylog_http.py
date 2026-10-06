"""`LogStore` implementation backed by the Graylog REST API."""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.models import (
    Between,
    ClusterInfo,
    LastSeconds,
    LogEntry,
    LogQuery,
    SearchPage,
    Stream,
)
from .graylog_api import GraylogApi, build_http_client, integer, objects, segment

__all__ = ["GraylogStore", "build_http_client"]

_SEARCH_PATHS = {
    LastSeconds: "/api/search/universal/relative",
    Between: "/api/search/universal/absolute",
}


def _unwrap(hit: dict[str, Any]) -> dict[str, Any]:
    """Search hits nest fields under 'message'; single-message lookups too."""
    inner = hit.get("message")
    return inner if isinstance(inner, dict) else hit


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

    def streams(self) -> list[Stream]:
        body = self._api.get_object("/api/streams")
        return [
            Stream(
                id=item["id"],
                title=item.get("title"),
                description=item.get("description"),
                disabled=bool(item.get("disabled", False)),
                is_default=bool(item.get("is_default", False)),
            )
            for item in objects(body.get("streams"))
            if "id" in item
        ]

    def search(self, query: LogQuery) -> SearchPage:
        body = self._api.get_object(
            _SEARCH_PATHS[type(query.window)], params=self._search_params(query)
        )
        entries = tuple(
            LogEntry(_unwrap(hit), index=_index(hit)) for hit in objects(body.get("messages"))
        )
        return SearchPage(total_hits=integer(body.get("total_results")) or 0, entries=entries)

    def fetch(self, index: str, message_id: str) -> LogEntry:
        return LogEntry(
            _unwrap(self._api.get_object(f"/api/messages/{segment(index)}/{segment(message_id)}")),
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
        window = query.window
        if isinstance(window, LastSeconds):
            params["range"] = window.seconds
        else:
            params["from"] = window.start
            params["to"] = window.end
        if query.only_fields:
            params["fields"] = ",".join(query.only_fields)
        return params
