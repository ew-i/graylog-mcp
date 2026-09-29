"""Pure functions that derive insight from already-fetched log entries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .models import LogEntry


@dataclass(frozen=True)
class ValueCount:
    value: str
    count: int


def rank_values(entries: Iterable[LogEntry], field_name: str, top: int) -> list[ValueCount]:
    """Count occurrences of each value of `field_name`, most frequent first.

    Entries lacking the field (or holding null) are ignored. Values are
    compared by their string form so that e.g. 500 and "500" merge. Ties
    keep first-seen order, which makes the output deterministic.
    """
    tally: Counter[str] = Counter(
        str(entry.fields[field_name])
        for entry in entries
        if entry.fields.get(field_name) is not None
    )
    return [ValueCount(value, count) for value, count in tally.most_common(top)]


def field_names(entries: Iterable[LogEntry]) -> list[str]:
    """Sorted union of field names across all entries."""
    names: set[str] = set()
    for entry in entries:
        names.update(entry.fields)
    return sorted(names)
