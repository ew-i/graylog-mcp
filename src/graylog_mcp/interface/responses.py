"""Turns use-case outcomes into the text payload every tool returns."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ..domain.errors import GraylogMcpError, error_payload
from .redaction import redact

_log = logging.getLogger(__name__)


def respond(action: Callable[[], Any]) -> str:
    """Run a use case; any failure becomes an {"error", "kind"} payload instead of raising."""
    try:
        payload = action()
    except Exception as exc:
        if not isinstance(exc, GraylogMcpError):
            _log.exception("tool failed unexpectedly")
        payload = error_payload(exc)
    return json.dumps(redact(payload), indent=2, default=str)
