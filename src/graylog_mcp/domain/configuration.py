"""Read-only view of how Graylog routes, processes and retains messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Graylog's StreamRuleType codes -> (name, verb phrase used in descriptions)
STREAM_RULE_TYPES = {
    1: ("exact", "match exactly"),
    2: ("regex", "match regular expression"),
    3: ("greater", "be greater than"),
    4: ("smaller", "be smaller than"),
    5: ("presence", "be present"),
    6: ("contains", "contain"),
    7: ("always_match", "always match"),
    8: ("match_input", "match input"),
}


@dataclass(frozen=True)
class StreamRule:
    field: str | None
    type_code: int | None
    value: str | None
    inverted: bool
    description: str | None

    @property
    def type_name(self) -> str:
        return STREAM_RULE_TYPES.get(self.type_code, (f"type_{self.type_code}", ""))[0]

    def meaning(self) -> str:
        """Human-readable rule, e.g. 'level must match exactly 3'."""
        name, verb = STREAM_RULE_TYPES.get(self.type_code, (None, None))
        must = "must not" if self.inverted else "must"
        field_name = self.field or "?"
        if name == "always_match":
            return "every message matches"
        if name == "presence":
            return f"{field_name} {must} be present"
        if name == "match_input":
            return f"input {must} be {self.value!r}"
        if name is None:
            return (
                f"{field_name} {must} satisfy unknown rule type {self.type_code} "
                f"with {self.value!r}"
            )
        return f"{field_name} {must} {verb} {self.value!r}"


@dataclass(frozen=True)
class StreamConfig:
    id: str
    title: str | None
    description: str | None
    matching_type: str | None  # "AND" or "OR"
    disabled: bool
    index_set_id: str | None
    rules: tuple[StreamRule, ...]


@dataclass(frozen=True)
class PipelineStage:
    stage: int
    match: str | None  # "ALL", "EITHER", "PASS"
    rules: tuple[str, ...]  # rule titles


@dataclass(frozen=True)
class Pipeline:
    id: str
    title: str | None
    description: str | None
    stages: tuple[PipelineStage, ...]


@dataclass(frozen=True)
class PipelineConnection:
    stream_id: str
    pipeline_ids: tuple[str, ...]


@dataclass(frozen=True)
class PipelineRule:
    id: str
    title: str
    description: str | None
    source: str | None


@dataclass(frozen=True)
class Strategy:
    name: str | None  # short class name, e.g. DeletionRetentionStrategy
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexSet:
    id: str
    title: str | None
    description: str | None
    index_prefix: str | None
    is_default: bool
    writable: bool
    shards: int | None
    replicas: int | None
    rotation: Strategy
    retention: Strategy
    data_tiering: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LookupTable:
    id: str | None
    name: str
    title: str | None
    description: str | None


@dataclass(frozen=True)
class LookupResult:
    table: str
    key: str
    single_value: Any
    multi_value: Any
    string_list_value: Any
    has_error: bool
    ttl: int | None
