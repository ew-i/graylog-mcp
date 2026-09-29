"""Saved searches and dashboards (Graylog 'views')."""

from __future__ import annotations

from dataclasses import dataclass

from .models import TimeWindow


@dataclass(frozen=True)
class ViewSummary:
    id: str
    title: str | None
    description: str | None
    summary: str | None
    owner: str | None
    last_updated: str | None


@dataclass(frozen=True)
class SavedQuery:
    """The runnable part of a saved search: query text, streams and time range."""

    view_id: str
    title: str | None
    query_text: str
    stream_ids: tuple[str, ...]
    window: TimeWindow | None  # None when the range cannot be expressed as a window
    keyword: str | None = None  # e.g. "last five minutes" for keyword ranges
