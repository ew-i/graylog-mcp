"""Self-contained continuation cursors and common pagination metadata.

A cursor carries the normalised request that produced it plus the offset of
the next page, so a follow-up call needs nothing but the cursor. Relative time
windows are pinned to absolute timestamps when the first page is requested,
so every page of one result set reads the same slice of time even while new
messages keep arriving.

Cursors are opaque to callers but not signed. They only spare the caller from
repeating arguments; every value read back is validated and clamped again.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..domain.errors import InvalidRequestError
from ..domain.models import Between, JsonDict, LastSeconds, ListingPage, ListingQuery, TimeWindow
from ..domain.timeutil import format_timestamp
from .policy import clamp

_INVALID = "next_cursor is invalid"


@dataclass(frozen=True)
class Page:
    """Which page of which request to fetch."""

    request: JsonDict  # normalised request; always holds "kind" and "limit"
    offset: int
    requested_limit: int | None = None  # the caller's limit on a first page, before clamping

    @property
    def limit(self) -> int:
        return self.request["limit"]

    def value(self, name: str, kind: type = str) -> Any:
        """A stored request value, checked because a cursor may have been edited."""
        value = self.request.get(name)
        if not isinstance(value, kind):
            raise InvalidRequestError(_INVALID)
        return value

    def window(self) -> Between:
        stored = self.value("window", dict)
        start, end = stored.get("from"), stored.get("to")
        if not isinstance(start, str) or not isinstance(end, str):
            raise InvalidRequestError(_INVALID)
        return Between(start, end)


def pinned_window(window: TimeWindow, now: datetime) -> JsonDict:
    """Fix a relative window to absolute timestamps; absolute windows pass through."""
    if isinstance(window, LastSeconds):
        return {
            "from": format_timestamp(now - timedelta(seconds=window.seconds)),
            "to": format_timestamp(now),
            "relative_seconds": window.seconds,
        }
    return window.describe()


def open_page(
    kind: str,
    next_cursor: str,
    *,
    limit: int,
    max_limit: int,
    match: JsonDict | None = None,
    details: Callable[[], JsonDict] = dict,
) -> Page:
    """Start a new result set, or resume the one `next_cursor` belongs to.

    `match` holds the arguments that identify the result set (query text,
    stream, filters). A first page stores them with `details()` and the
    clamped limit. When resuming, the request stored in the cursor is used;
    any `match` value the caller filled in must equal the stored one, so a
    cursor from one search cannot silently continue a different one.
    """
    match = match or {}
    if not next_cursor:
        bounded = clamp(limit, 1, max_limit)
        return Page({"kind": kind, **match, **details(), "limit": bounded}, 0, limit)

    request, offset = _decode(next_cursor)
    if request.get("kind") != kind:
        raise InvalidRequestError("next_cursor belongs to a different tool")
    for name, value in match.items():
        if value not in ("", None) and request.get(name) != value:
            raise InvalidRequestError(
                f"next_cursor belongs to a request with a different {name}; "
                "omit next_cursor to start a new search"
            )
    request["limit"] = clamp(request["limit"], 1, max_limit)
    return Page(request, offset)


def page_metadata(
    page: Page,
    returned: int,
    total: int,
    *,
    next_offset: int,
    reachable: int | None = None,
) -> JsonDict:
    """Continuation and truncation fields shared by every paginated response.

    `has_more` means `next_cursor` fetches more. `truncated` means the caller
    is not getting everything it asked for, and `truncation` says why: the
    requested limit was above the maximum page size, or matches lie deeper
    than the backend can page (`reachable`, e.g. OpenSearch's result window).

    `next_offset` is where the following page starts, as reported by the
    store (see `SearchPage.next_offset`); it can lie beyond `offset + returned`
    when the store dropped malformed entries.
    """
    remaining = total > next_offset
    blocked = remaining and reachable is not None and next_offset + page.limit > reachable
    has_more = remaining and returned > 0 and not blocked

    reasons = []
    if remaining and page.requested_limit is not None and page.requested_limit > page.limit:
        reasons.append(
            f"The requested limit {page.requested_limit} is above the maximum page size "
            f"of {page.limit}; follow next_cursor for the rest."
        )
    if blocked:
        reasons.append(
            f"Only the first {next_offset} of {total} matches can be paged to; "
            "narrow the query or time range to see the rest."
        )

    metadata: JsonDict = {
        "limit": page.limit,
        "next_cursor": _encode(page.request, next_offset) if has_more else None,
        "has_more": has_more,
        "truncated": bool(reasons),
    }
    if reasons:
        metadata["truncation"] = " ".join(reasons)
    return metadata


def listing(
    kind: str,
    next_cursor: str,
    *,
    text: str,
    limit: int,
    max_limit: int,
    fetch: Callable[[ListingQuery], ListingPage],
) -> tuple[ListingPage, JsonDict]:
    """One page of a catalogue listing filtered by title text.

    Returns the store's page and the paging metadata to merge into the response.
    """
    page = open_page(
        kind, next_cursor, limit=limit, max_limit=max_limit, match={"text": text.strip()}
    )
    found = fetch(ListingQuery(limit=page.limit, text=page.value("text"), offset=page.offset))
    return found, page_metadata(page, len(found.items), found.total, next_offset=found.next_offset)


def _encode(request: JsonDict, offset: int) -> str:
    raw = json.dumps({"request": request, "offset": offset}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[JsonDict, int]:
    try:
        padded = cursor.strip().encode() + b"=" * (-len(cursor.strip()) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        raise InvalidRequestError(_INVALID) from None
    if not isinstance(payload, dict):
        raise InvalidRequestError(_INVALID)
    request, offset = payload.get("request"), payload.get("offset")
    if (
        not isinstance(request, dict)
        or not _is_count(offset)
        or not _is_count(request.get("limit"))
        or request["limit"] < 1
    ):
        raise InvalidRequestError(_INVALID)
    return request, offset


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
