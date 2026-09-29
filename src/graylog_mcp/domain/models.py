"""Value objects describing log queries and their results.

Each object checks its own invariants on construction, so an instance that
exists is always valid. Callers decide *policy* (defaults, clamping); the
models only reject values that are meaningless.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import InvalidRequestError

JsonDict = dict[str, Any]


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"

    @classmethod
    def parse(cls, raw: str) -> SortDirection:
        try:
            return cls(raw.strip().lower())
        except ValueError:
            raise InvalidRequestError(f"sort order must be 'asc' or 'desc', got {raw!r}") from None


@dataclass(frozen=True)
class LastSeconds:
    """A window ending now and reaching `seconds` into the past."""

    seconds: int

    def __post_init__(self) -> None:
        if self.seconds < 1:
            raise InvalidRequestError("time window must be at least one second")

    def describe(self) -> JsonDict:
        return {"relative_seconds": self.seconds}


@dataclass(frozen=True)
class Between:
    """A fixed window between two ISO-8601 timestamps."""

    start: str
    end: str

    def __post_init__(self) -> None:
        if not self.start.strip() or not self.end.strip():
            raise InvalidRequestError("both a start and an end timestamp are required")

    def describe(self) -> JsonDict:
        return {"from": self.start, "to": self.end}


TimeWindow = LastSeconds | Between


@dataclass(frozen=True)
class Sort:
    field: str = "timestamp"
    direction: SortDirection = SortDirection.DESC

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise InvalidRequestError("sort field must not be blank")

    def as_param(self) -> str:
        return f"{self.field}:{self.direction.value}"


def require_scope(text: str, stream_id: str) -> None:
    """Every query needs query text and a stream (Graylog grants access per stream)."""
    if not text.strip():
        raise InvalidRequestError("query text must not be blank")
    if not stream_id.strip():
        raise InvalidRequestError(
            "a stream_id is required because access is granted per stream; "
            "call browse_streams to find one"
        )


@dataclass(frozen=True)
class LogQuery:
    """Everything needed to run one search against a single stream."""

    text: str
    stream_id: str
    window: TimeWindow
    max_results: int
    sort: Sort = field(default_factory=Sort)
    only_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_scope(self.text, self.stream_id)
        if self.max_results < 1:
            raise InvalidRequestError("max_results must be positive")


@dataclass(frozen=True)
class LogEntry:
    """One log message as a flat field mapping."""

    fields: Mapping[str, Any]

    _HIDDEN_PREFIXES = ("gl2_",)
    _HIDDEN_KEYS = frozenset({"_id", "streams"})

    def visible(self, only: tuple[str, ...] = ()) -> JsonDict:
        """Return the fields a human cares about.

        With `only`, exactly those fields (that exist) are returned, which
        lets callers ask for internal fields explicitly. Without it,
        Graylog bookkeeping fields are dropped.
        """
        if only:
            return {name: self.fields[name] for name in only if name in self.fields}
        return {
            name: value
            for name, value in self.fields.items()
            if name not in self._HIDDEN_KEYS and not name.startswith(self._HIDDEN_PREFIXES)
        }


@dataclass(frozen=True)
class SearchPage:
    total_hits: int
    entries: tuple[LogEntry, ...]


@dataclass(frozen=True)
class Stream:
    id: str
    title: str | None
    description: str | None
    disabled: bool
    is_default: bool


@dataclass(frozen=True)
class ClusterInfo:
    version: str | None
    codename: str | None
    cluster_id: str | None
    node_id: str | None
    hostname: str | None
    is_processing: bool | None
    timezone: str | None
