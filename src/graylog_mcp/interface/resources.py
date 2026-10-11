"""Read-only MCP resources: stable Graylog reference data a client can attach as context.

Each Graylog-backed resource returns the same JSON (redacted, errors as
`{"error", "kind"}`) as the matching tool. Successful reads are cached briefly
so repeated reads do not refetch; failures are never cached. Tools stay
uncached for when fresh data matters. The query guide is static text.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer

from ..application.policy import Limits
from .query_guide import QUERY_SYNTAX
from .responses import Responder

if TYPE_CHECKING:
    from .mcp_tools import ServiceBundle

CACHE_SECONDS = 60.0  # default; MCP_RESOURCE_CACHE_SECONDS / --resource-cache-seconds
# Catalogue resources return one page of the listing maximum; `has_more` and
# `next_cursor` say when the matching tool has more.
_CATALOGUE_LIMIT = 200
# Field discovery samples only the newest messages, so the longest allowed window
# costs little more and still finds fields in streams that have been quiet for days.
_FIELDS_WINDOW_SECONDS = Limits().max_window_seconds
_JSON = "application/json"


class TtlCache:
    """Keeps each successful load for `ttl` seconds.

    MCP runs resource reads in worker threads, so the entries are guarded by a
    lock; `load` runs outside it, so one slow Graylog call blocks no other read.
    """

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]
        value = load()  # an exception propagates and leaves the cache unchanged
        with self._lock:
            # Drop expired entries so per-stream keys cannot accumulate forever.
            self._entries = {k: v for k, v in self._entries.items() if now - v[0] < self.ttl}
            self._entries[key] = (now, value)
        return value


def register(
    server: MCPServer,
    services: ServiceBundle,
    respond: Responder,
    cache: TtlCache | None = None,
) -> None:
    cache = cache or TtlCache(CACHE_SECONDS)
    freshness = f"Cached for up to {cache.ttl:g} s." if cache.ttl > 0 else "Not cached."
    catalogue: dict[str, tuple[str, str, Callable[[], Any]]] = {
        "graylog://streams": (
            "streams",
            "Streams you may query, with IDs (same as browse_streams).",
            lambda: services.search.streams(limit=_CATALOGUE_LIMIT),
        ),
        "graylog://saved-searches": (
            "saved-searches",
            "Saved searches with IDs for run_saved_search (same as saved_searches).",
            lambda: services.views.saved_searches(limit=_CATALOGUE_LIMIT),
        ),
        "graylog://cluster/status": (
            "cluster-status",
            "Graylog version, cluster identity and processing state (same as cluster_status).",
            services.search.cluster_info,
        ),
        "graylog://cluster/capabilities": (
            "cluster-capabilities",
            "Graylog version, which optional APIs it offers and which tools it cannot serve "
            "(same as server_capabilities).",
            services.capabilities.server_capabilities,
        ),
        "graylog://alerts/definitions": (
            "alert-definitions",
            "Configured event/alert definitions (same as alert_definitions).",
            lambda: services.alerts.alert_definitions(limit=_CATALOGUE_LIMIT),
        ),
        "graylog://config/index-sets": (
            "index-sets",
            "Index sets with rotation and retention (same as index_sets).",
            services.configuration.index_sets,
        ),
        "graylog://config/pipelines": (
            "pipelines",
            "Processing pipelines, stages and rules (same as pipeline_rules).",
            services.configuration.pipeline_rules,
        ),
    }
    for uri, (name, description, load) in catalogue.items():
        _add(server, respond, cache, uri, name, f"{description} {freshness}", load)

    @server.resource(
        "graylog://streams/{stream_id}/fields",
        name="stream-fields",
        description=(
            "A stream's fields over the last "
            f"{_FIELDS_WINDOW_SECONDS // 86400} days with type, groupable and numeric "
            f"(same as discover_fields). {freshness}"
        ),
        mime_type=_JSON,
    )
    def stream_fields(stream_id: str) -> str:
        return respond(
            lambda: cache.get(
                f"graylog://streams/{stream_id}/fields",
                lambda: services.search.discover_fields(
                    stream_id=stream_id, seconds=_FIELDS_WINDOW_SECONDS
                ),
            )
        )

    @server.resource(
        "graylog://guide/query-syntax",
        name="query-syntax",
        description="Graylog query syntax and the recommended way to use this server's tools.",
        mime_type="text/markdown",
    )
    def query_syntax() -> str:
        return QUERY_SYNTAX


def _add(
    server: MCPServer,
    respond: Responder,
    cache: TtlCache,
    uri: str,
    name: str,
    description: str,
    load: Callable[[], Any],
) -> None:
    @server.resource(uri, name=name, description=description, mime_type=_JSON)
    def read() -> str:
        return respond(lambda: cache.get(uri, load))
