"""One log line per tool call: who called which tool with which arguments,
how long it took and how it ended. Arguments are redacted and shortened."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .responses import shorten

if TYPE_CHECKING:
    from collections.abc import Callable

    from .redaction import Redactor

_log = logging.getLogger("graylog_mcp.audit")
_ARGUMENT_CHARS = 500


def tool_call_logger(redactor: Redactor, caller_id: Callable[[], str]) -> Callable[..., Any]:

    async def log_tool_calls(ctx: Any, call_next: Callable[[Any], Any]) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        started = time.perf_counter()
        outcome, kind = "exception", None
        try:
            result = await call_next(ctx)
            outcome, kind = _outcome(result)
            return result
        finally:
            params = ctx.params if isinstance(ctx.params, dict) else {}
            entry = {
                "tool": params.get("name"),
                "caller": caller_id()[:12] or None,
                "arguments": shorten(
                    redactor.redact(params.get("arguments") or {}), _ARGUMENT_CHARS
                ),
                "ms": round((time.perf_counter() - started) * 1000),
                "outcome": outcome,
                "kind": kind,
            }
            _log.info("tool_call %s", json.dumps(entry, ensure_ascii=False, default=str))

    return log_tool_calls


def _outcome(result: Any) -> tuple[str, str | None]:
    """("ok", None), or ("error", kind) with kind from our {"error", "kind"} payload."""
    if not isinstance(result, dict) or not result.get("isError"):
        return "ok", None
    try:
        kind = json.loads(result["content"][0]["text"]).get("kind")
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        kind = None
    return "error", kind
