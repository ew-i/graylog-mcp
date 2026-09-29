"""Timestamp parsing/formatting in the shapes Graylog uses."""

from __future__ import annotations

from datetime import datetime, timezone

from .errors import InvalidRequestError


def parse_timestamp(raw: str) -> datetime:
    """Accepts '2026-03-15T10:00:00.123Z', '2026-03-15 10:00:00.123' and offsets; returns UTC."""
    text = str(raw).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise InvalidRequestError(f"unrecognised timestamp {raw!r}") from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """UTC ISO-8601 with millisecond precision, the format Graylog accepts in queries."""
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
