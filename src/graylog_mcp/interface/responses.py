"""Turns use-case outcomes into the text payload every tool returns."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..domain.errors import GraylogMcpError


def respond(action: Callable[[], Any]) -> str:
    """Run a use case; expected failures become an {"error", "kind"} payload instead of raising."""
    try:
        payload = action()
    except GraylogMcpError as exc:
        payload = {"error": str(exc), "kind": exc.kind}
    return json.dumps(payload, indent=2, default=str)
