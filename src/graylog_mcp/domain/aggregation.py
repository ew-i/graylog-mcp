"""Value objects for server-side aggregations (counts, histograms, statistics)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import InvalidRequestError
from .models import TimeWindow, require_scope

_INTERVAL = re.compile(r"^([1-9][0-9]*)([smhdwMy])$")
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "M": 2_592_000,
    "y": 31_536_000,
}
_AUTO_LADDER = ("10s", "30s", "1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d", "1w", "1M")
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_.@-]+$")

METRIC_FUNCTIONS = frozenset({"count", "avg", "min", "max", "sum", "card", "percentile"})


def require_field_name(name: str, what: str = "field") -> str:
    name = name.strip()
    if not name:
        raise InvalidRequestError(f"{what} must not be blank")
    if not _FIELD_NAME.match(name):
        raise InvalidRequestError(
            f"{what} {name!r} contains characters that are not allowed in a field name"
        )
    return name


def interval_seconds(interval: str) -> int:
    match = _INTERVAL.match(interval)
    if not match:
        raise InvalidRequestError(
            f"interval must look like 30s, 5m, 1h, 1d, 1w, 1M or 1y, got {interval!r}"
        )
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def auto_interval(window_seconds: int, max_buckets: int) -> str:
    """Smallest 'round' interval that keeps the bucket count within max_buckets."""
    for candidate in _AUTO_LADDER:
        if window_seconds / interval_seconds(candidate) <= max_buckets:
            return candidate
    return "1y"


@dataclass(frozen=True)
class ValuesGrouping:
    """Group by the distinct values of a field, keeping the `limit` largest groups."""

    field: str
    limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", require_field_name(self.field))
        if self.limit < 1:
            raise InvalidRequestError("group limit must be positive")


@dataclass(frozen=True)
class TimeGrouping:
    """Group into fixed time buckets (a histogram)."""

    interval: str
    field: str = "timestamp"

    def __post_init__(self) -> None:
        interval_seconds(self.interval)  # validates


Grouping = ValuesGrouping | TimeGrouping


@dataclass(frozen=True)
class Metric:
    function: str
    field: str | None = None
    percentile: float | None = None
    descending: bool = False

    def __post_init__(self) -> None:
        if self.function not in METRIC_FUNCTIONS:
            raise InvalidRequestError(f"unknown metric function {self.function!r}")
        if self.field is not None:
            object.__setattr__(self, "field", require_field_name(self.field))
        elif self.function != "count":
            raise InvalidRequestError(f"metric {self.function} needs a field")
        if self.function == "percentile":
            if self.percentile is None or not 0 < self.percentile <= 100:
                raise InvalidRequestError("percentile must be in (0, 100]")
        elif self.percentile is not None:
            raise InvalidRequestError("only the percentile metric takes a percentile value")

    @property
    def label(self) -> str:
        if self.function == "percentile":
            p = self.percentile
            name = f"p{int(p)}" if p == int(p) else f"p{p}"
        else:
            name = self.function
        return name if self.field is None else f"{name}({self.field})"


@dataclass(frozen=True)
class AggregationQuery:
    """An aggregation over one stream. An empty `groups` means 'one row for everything'."""

    text: str
    stream_id: str
    window: TimeWindow
    groups: tuple[Grouping, ...]
    metrics: tuple[Metric, ...]

    def __post_init__(self) -> None:
        require_scope(self.text, self.stream_id)
        if not self.metrics:
            raise InvalidRequestError("at least one metric is required")
        labels = [m.label for m in self.metrics]
        if len(set(labels)) != len(labels):
            raise InvalidRequestError("metrics must be distinct")


@dataclass(frozen=True)
class AggregationRow:
    keys: tuple[str | None, ...]  # one per grouping, in query order
    values: Mapping[str, object]  # metric label -> value (None when unavailable)


@dataclass(frozen=True)
class AggregationTable:
    rows: tuple[AggregationRow, ...]
