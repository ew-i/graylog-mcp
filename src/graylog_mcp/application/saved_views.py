"""Saved searches and dashboards."""

from __future__ import annotations

from dataclasses import asdict

from ..domain.errors import InvalidRequestError
from ..domain.models import Between, JsonDict, LastSeconds
from .pagination import listing
from .policy import Limits
from .ports import ViewStore
from .service import LogService

_FALLBACK_SECONDS = 900


class SavedViewService:
    def __init__(self, views: ViewStore, search: LogService, limits: Limits | None = None) -> None:
        self._views = views
        self._search = search
        self._limits = limits or Limits()

    def saved_searches(
        self, *, text: str = "", limit: int = 50, next_cursor: str = ""
    ) -> JsonDict:
        found, paging = listing(
            "saved_searches",
            next_cursor,
            text=text,
            limit=limit,
            max_limit=self._limits.max_listing,
            fetch=self._views.saved_searches,
        )
        return {
            "total": found.total,
            "returned": len(found.items),
            "saved_searches": [asdict(i) for i in found.items],
            **paging,
        }

    def dashboards(self, *, text: str = "", limit: int = 50, next_cursor: str = "") -> JsonDict:
        found, paging = listing(
            "dashboards",
            next_cursor,
            text=text,
            limit=limit,
            max_limit=self._limits.max_listing,
            fetch=self._views.dashboards,
        )
        return {
            "total": found.total,
            "returned": len(found.items),
            "dashboards": [asdict(i) for i in found.items],
            **paging,
        }

    def run_saved_search(
        self,
        *,
        view_id: str,
        stream_id: str = "",
        limit: int = 50,
        fields: str = "",
        next_cursor: str = "",
    ) -> JsonDict:
        if not view_id.strip():
            raise InvalidRequestError("view_id is required; call saved_searches to find one")
        saved = self._views.saved_query(view_id.strip())
        notes: list[str] = []

        chosen = stream_id.strip()
        if not chosen and not next_cursor:
            if not saved.stream_ids:
                raise InvalidRequestError(
                    "this saved search is not limited to a stream; "
                    "pass stream_id (see browse_streams)"
                )
            chosen = saved.stream_ids[0]
            if len(saved.stream_ids) > 1:
                notes.append(
                    f"The saved search covers {len(saved.stream_ids)} streams; "
                    f"results are for {chosen}. Pass stream_id to pick another."
                )

        text = saved.query_text.strip() or "*"
        window = saved.window
        if isinstance(window, Between):
            result = self._search.search_between(
                text=text,
                stream_id=chosen,
                start=window.start,
                end=window.end,
                limit=limit,
                fields=fields,
                next_cursor=next_cursor,
            )
        else:
            seconds = window.seconds if isinstance(window, LastSeconds) else _FALLBACK_SECONDS
            if window is None:
                notes.append(
                    f"The saved time range ({saved.keyword or 'unknown type'}) was replaced by "
                    f"the last {_FALLBACK_SECONDS} seconds."
                )
            result = self._search.search_recent(
                text=text,
                stream_id=chosen,
                seconds=seconds,
                limit=limit,
                fields=fields,
                next_cursor=next_cursor,
            )

        return {
            "saved_search": {
                "id": saved.view_id,
                "title": saved.title,
                "query": text,
                "stream_ids": list(saved.stream_ids),
                "window": window.describe() if window else {"keyword": saved.keyword},
            },
            "stream_id": result["stream_id"],
            "notes": notes,
            "result": result,
        }
