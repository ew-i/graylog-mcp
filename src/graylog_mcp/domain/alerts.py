"""Events and alerts raised by Graylog's event definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidRequestError
from .models import TimeWindow

_PRIORITY_LABELS = {1: "low", 2: "normal", 3: "high", 4: "critical"}


def priority_label(priority: int | None) -> str | None:
    if priority is None:
        return None
    return _PRIORITY_LABELS.get(priority, str(priority))


@dataclass(frozen=True)
class EventQuery:
    window: TimeWindow
    limit: int
    text: str = ""
    alerts_only: bool = True
    definition_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise InvalidRequestError("limit must be positive")


@dataclass(frozen=True)
class Event:
    id: str
    definition_id: str | None
    definition_title: str | None
    timestamp: str | None
    message: str | None
    priority: int | None
    alert: bool
    source: str | None
    key: str | None
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventPage:
    total: int
    events: tuple[Event, ...]


@dataclass(frozen=True)
class EventDefinition:
    id: str
    title: str | None
    description: str | None
    priority: int | None
    alert: bool
    kind: str | None  # e.g. "aggregation-v1"
    query: str | None
    stream_ids: tuple[str, ...]
    search_within_ms: int | None
    execute_every_ms: int | None
