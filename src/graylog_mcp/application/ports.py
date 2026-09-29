"""Interfaces the application needs from the outside world.

Implementations must signal failure only with
`graylog_mcp.domain.errors.BackendError` (or subclasses).
"""

from __future__ import annotations

from typing import Protocol

from ..domain.aggregation import AggregationQuery, AggregationTable
from ..domain.alerts import EventDefinition, EventPage, EventQuery
from ..domain.configuration import (
    IndexSet,
    LookupResult,
    LookupTable,
    Pipeline,
    PipelineConnection,
    PipelineRule,
    StreamConfig,
)
from ..domain.models import ClusterInfo, LogEntry, LogQuery, SearchPage, Stream
from ..domain.operations import InputStatus, NodeStatus, NodeThroughput, SystemNotification
from ..domain.views import SavedQuery, ViewSummary


class LogStore(Protocol):
    """Message search and retrieval."""

    def cluster_info(self) -> ClusterInfo: ...

    def streams(self) -> list[Stream]: ...

    def search(self, query: LogQuery) -> SearchPage: ...

    def fetch(self, index: str, message_id: str) -> LogEntry: ...


class AggregationStore(Protocol):
    """Server-side counting and statistics."""

    def aggregate(self, query: AggregationQuery) -> AggregationTable: ...


class AlertStore(Protocol):
    def events(self, query: EventQuery) -> EventPage: ...

    def definitions(self, text: str, limit: int) -> list[EventDefinition]: ...


class ViewStore(Protocol):
    def saved_searches(self, text: str, limit: int) -> list[ViewSummary]: ...

    def dashboards(self, text: str, limit: int) -> list[ViewSummary]: ...

    def saved_query(self, view_id: str) -> SavedQuery: ...


class SystemStore(Protocol):
    def inputs(self) -> list[InputStatus]: ...

    def throughput(self) -> list[NodeThroughput]: ...

    def notifications(self) -> list[SystemNotification]: ...

    def nodes(self) -> list[NodeStatus]: ...


class ConfigStore(Protocol):
    def stream(self, stream_id: str) -> StreamConfig: ...

    def pipelines(self) -> list[Pipeline]: ...

    def pipeline_connections(self) -> list[PipelineConnection]: ...

    def pipeline_rules(self) -> list[PipelineRule]: ...

    def index_sets(self) -> list[IndexSet]: ...

    def lookup_tables(self, text: str, limit: int) -> list[LookupTable]: ...

    def lookup(self, table: str, key: str) -> LookupResult: ...
