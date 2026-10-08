"""Shared policy: upper bounds, clamping and argument normalisation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..domain.errors import InvalidRequestError


@dataclass(frozen=True)
class Limits:
    max_results: int = 1000
    max_window_seconds: int = 30 * 24 * 3600
    max_top_values: int = 100
    aggregation_sample: int = 1000
    field_discovery_sample: int = 50
    max_buckets: int = 200
    default_buckets: int = 48
    compare_candidates: int = 100
    max_context: int = 50
    max_context_window_seconds: int = 24 * 3600
    max_events: int = 200
    max_listing: int = 200
    # OpenSearch's default index.max_result_window: offset + size may not exceed it.
    max_result_window: int = 10_000


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def parse_field_list(raw: str) -> tuple[str, ...]:
    """'a, b,,c' -> ('a', 'b', 'c'); duplicates removed, order kept."""
    seen: dict[str, None] = {}
    for part in raw.split(","):
        name = part.strip()
        if name:
            seen.setdefault(name)
    return tuple(seen)


def parse_percentiles(raw: str) -> tuple[float, ...]:
    """'50, 90,99.9' -> (50.0, 90.0, 99.9); each must be in (0, 100]."""
    values: list[float] = []
    for part in parse_field_list(raw):
        try:
            value = float(part)
        except ValueError:
            raise InvalidRequestError(f"percentile {part!r} is not a number") from None
        if not 0 < value <= 100:
            raise InvalidRequestError(f"percentile {part} must be in (0, 100]")
        if value not in values:
            values.append(value)
    return tuple(values)
