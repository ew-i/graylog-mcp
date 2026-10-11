"""Turns use-case outcomes into the text payload every tool returns."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import Any

from ..domain.errors import GraylogMcpError, error_payload
from .redaction import Redactor

_log = logging.getLogger(__name__)


class Responder:
    """Runs a use case; any failure becomes an {"error", "kind"} payload instead of raising."""

    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    def __call__(self, action: Callable[[], Any], fields: Iterable[str] = ()) -> str:
        """`fields` names the fields whose values the use case reports, such as the
        field ranked by rank_field_values; values of a sensitive one are masked."""
        try:
            payload = action()
        except Exception as exc:
            if not isinstance(exc, GraylogMcpError):
                _log.exception("tool failed unexpectedly")
            payload = error_payload(exc)
        return json.dumps(self._redactor.redact(payload, fields), indent=2, default=str)
