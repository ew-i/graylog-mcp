"""Turns use-case outcomes into the payload every tool and resource returns."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

from ..domain.errors import GraylogMcpError, error_payload

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .redaction import Redactor

_log = logging.getLogger(__name__)

# A string value longer than this is cut, with a marker saying how much is missing.
MAX_VALUE_CHARS = 10_000
# When a whole response is longer than this, values are cut shorter until it fits
# (down to MIN_VALUE_CHARS). Lists are never cut, so paging stays exact.
MAX_RESPONSE_CHARS = 200_000
MIN_VALUE_CHARS = 500


class Responder:
    """Runs a use case; any failure becomes an {"error", "kind"} payload instead of raising.

    Calling it gives a tool result, flagged `is_error` on failure; `text` gives the
    same JSON as plain text, for resources.
    """

    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    def __call__(self, action: Callable[[], Any], fields: Iterable[str] = ()) -> CallToolResult:
        failed, text = self._run(action, fields)
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=failed)

    def text(self, action: Callable[[], Any], fields: Iterable[str] = ()) -> str:
        """`fields` names the fields whose values the use case reports, such as the
        field ranked by rank_field_values; values of a sensitive one are masked."""
        return self._run(action, fields)[1]

    def _run(self, action: Callable[[], Any], fields: Iterable[str]) -> tuple[bool, str]:
        try:
            payload, failed = action(), False
        except Exception as exc:
            if not isinstance(exc, GraylogMcpError):
                _log.exception("tool failed unexpectedly")
            payload, failed = error_payload(exc), True
        return failed, fit(self._redactor.redact(payload, fields))


def fit(payload: Any) -> str:
    """Serialize `payload`, cutting long string values until it fits the budget."""
    limit = MAX_VALUE_CHARS
    while True:
        text = _dumps(shorten(payload, limit))
        if len(text) <= MAX_RESPONSE_CHARS or limit <= MIN_VALUE_CHARS:
            return text
        limit = max(limit // 4, MIN_VALUE_CHARS)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def shorten(value: Any, limit: int) -> Any:
    """Cut string values and keys longer than `limit`, marking how much is missing."""
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}…[+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {_shorten_key(key, limit): shorten(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shorten(item, limit) for item in value]
    return value


def _shorten_key(key: Any, limit: int) -> Any:
    """Like shorten, plus a hash of the full key, so keys that differ only after the
    cut stay separate instead of overwriting each other's value."""
    if not isinstance(key, str) or len(key) <= limit:
        return key
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return f"{key[:limit]}…[+{len(key) - limit} chars, {digest}]"
