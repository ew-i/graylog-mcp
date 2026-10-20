"""Which optional Graylog APIs the server offers, and what each one means for the tools."""

from __future__ import annotations

from ..domain.capabilities import Capability
from ..domain.models import JsonDict
from .ports import CapabilityStore, StreamCatalog

# capability: (what it provides, what happens without it, tools that return Unsupported)
_EFFECTS: dict[Capability, tuple[str, str, tuple[str, ...]]] = {
    Capability.AGGREGATION: (
        "Server-side counting and statistics (Search Scripting API, Graylog 5.1+)",
        "exact_field_counts, field_stats, count_over_time and compare_windows with a field "
        "return Unsupported; count_matches and rank_field_values still work",
        ("count_over_time", "exact_field_counts", "field_stats"),
    ),
    Capability.TIME_GROUPING: (
        "Server-side counting per time bucket (not in Graylog 5.1)",
        "count_over_time returns Unsupported; call count_matches once per window instead",
        ("count_over_time",),
    ),
    Capability.FIELD_TYPES: (
        "Field types, and whether a field can be grouped or measured",
        "discover_fields lists names from recent messages, without types",
        (),
    ),
    Capability.QUERY_VALIDATION: (
        "Query syntax checks without running a search",
        "validate_query returns Unsupported; a mistyped query shows only as zero results",
        ("validate_query",),
    ),
    Capability.PAGINATED_STREAMS: (
        "Stream listing one page at a time",
        "browse_streams reads the whole stream list for every page",
        (),
    ),
}


class CapabilityService:
    def __init__(self, logs: StreamCatalog, store: CapabilityStore) -> None:
        self._logs = logs
        self._store = store

    def server_capabilities(self) -> JsonDict:
        version = self._logs.cluster_info().version
        found = self._store.capabilities()
        unsupported: set[str] = set()
        report: JsonDict = {}
        for capability, (provides, without, tools) in _EFFECTS.items():
            available = found.get(capability)
            report[capability.value] = {
                "available": available,
                "provides": provides,
                "without_it": without,
            }
            if available is False:
                unsupported.update(tools)
        result: JsonDict = {
            "version": version,
            "capabilities": report,
            "unsupported_tools": sorted(unsupported),
        }
        if None in found.values():
            result["note"] = (
                "available is null where Graylog did not answer the check; "
                "ask again once it is reachable."
            )
        return result
