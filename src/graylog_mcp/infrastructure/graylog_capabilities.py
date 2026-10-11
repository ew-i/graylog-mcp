"""Which optional Graylog APIs this server offers, learned by probing their routes.

Graylog answers a route it does not have with HTTP 404. Any other answer, even a
rejection of a probe's deliberately incomplete body or a refused permission,
means the API exists. No probe runs a search. Results are kept for `ttl`
seconds, so an upgraded server is noticed without a restart; `ttl=0` keeps
nothing. A probe Graylog did not answer (unreachable, timeout) is not kept.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..domain.capabilities import Capability
from ..domain.errors import BackendError, BackendUnavailableError, NotFoundError
from .graylog_api import GraylogApi

AGGREGATE_PATH = "/api/search/aggregate"
# How a Search Scripting API without time grouping (e.g. Graylog 5.1) rejects it.
NO_TIME_GROUPING = "Unable to map property timeunit"

_PROBES: dict[Capability, Callable[[GraylogApi], Any]] = {
    # No grouping and no metric: rejected as invalid wherever the API exists.
    Capability.AGGREGATION: lambda api: api.post(AGGREGATE_PATH, {}),
    # Without a metric this is invalid too, but 5.1 fails earlier, on `timeunit`.
    Capability.TIME_GROUPING: lambda api: api.post(
        AGGREGATE_PATH, {"group_by": [{"field": "timestamp", "timeunit": "1m"}]}
    ),
    Capability.FIELD_TYPES: lambda api: api.post("/api/views/fields", {"streams": []}),
    Capability.QUERY_VALIDATION: lambda api: api.post("/api/search/validate", {"query": "*"}),
    Capability.PAGINATED_STREAMS: lambda api: api.get(
        "/api/streams/paginated", params={"page": 1, "per_page": 1}
    ),
}


class GraylogCapabilities:
    """Shared by the adapters so each API is probed at most once per `ttl`."""

    def __init__(
        self, client: httpx.Client, ttl: float = 0.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._api = GraylogApi(client)
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._known: dict[Capability, tuple[float, bool]] = {}

    def known(self, capability: Capability) -> bool | None:
        """What was learned and is still fresh, without asking Graylog."""
        with self._lock:
            entry = self._known.get(capability)
        if entry is None or self._clock() - entry[0] >= self._ttl:
            return None
        return entry[1]

    def record(self, capability: Capability, available: bool) -> None:
        with self._lock:
            self._known[capability] = (self._clock(), available)

    def supports(self, capability: Capability) -> bool | None:
        """Known or probed availability; None when Graylog did not answer the probe."""
        available = self.known(capability)
        if available is not None:
            return available
        if (
            capability is Capability.TIME_GROUPING
            and self.supports(Capability.AGGREGATION) is False
        ):
            return False
        available = self._probe(capability)
        if available is not None:
            self.record(capability, available)
        return available

    def capabilities(self) -> dict[Capability, bool | None]:
        return {capability: self.supports(capability) for capability in Capability}

    def _probe(self, capability: Capability) -> bool | None:
        try:
            _PROBES[capability](self._api)
        except NotFoundError:
            return False
        except BackendUnavailableError:
            return None
        except BackendError as exc:
            return not (capability is Capability.TIME_GROUPING and NO_TIME_GROUPING in str(exc))
        return True
