"""Interfaces the application needs from the outside world.

Implementations must signal failure only with
`graylog_mcp.domain.errors.BackendError` (or subclasses).

Paging is by offset everywhere. A store whose backend pages by number
converts the offset itself and reports where the next page starts in the
result's `next_offset`, so use cases never see the backend's paging style.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.aggregation import AggregationQuery, AggregationTable
from ..domain.alerts import EventPage, EventQuery
from ..domain.capabilities import Capability
from ..domain.configuration import (
    IndexSet,
    LookupResult,
    Pipeline,
    PipelineConnection,
    PipelineRule,
    StreamConfig,
)
from ..domain.models import (
    ClusterInfo,
    FieldInfo,
    ListingPage,
    ListingQuery,
    LogEntry,
    LogQuery,
    QueryValidation,
    SearchPage,
    TimeWindow,
)
from ..domain.operations import InputStatus, NodeStatus, NodeThroughput, SystemNotification
from ..domain.views import SavedQuery


class StreamCatalog(Protocol):
    """Cluster identity and the readable streams."""

    def cluster_info(self) -> ClusterInfo: ...

    def streams(self, offset: int, limit: int) -> ListingPage:
        """Readable streams other than Graylog's default "All messages" stream."""
        ...

    def stream_exists(self, stream_id: str) -> bool: ...


class MessageStore(Protocol):
    """Message search and retrieval."""

    def search(self, query: LogQuery) -> SearchPage: ...

    def fetch(self, index: str, message_id: str) -> LogEntry: ...

    def field_types(self, stream_id: str, window: TimeWindow) -> list[FieldInfo]:
        """Indexed fields with their capabilities; UnsupportedError without the API."""
        ...

    def validate(self, query: str, stream_id: str, window: TimeWindow) -> QueryValidation:
        """Graylog's verdict on a query, without searching; UnsupportedError without the API."""
        ...


class LogStore(StreamCatalog, MessageStore, Protocol):
    """Both halves, for the search use case."""


class CapabilityStore(Protocol):
    def capabilities(self) -> dict[Capability, bool | None]:
        """Whether each optional API exists; None when the server did not answer."""
        ...


class AggregationStore(Protocol):
    """Server-side counting and statistics."""

    def aggregate(self, query: AggregationQuery) -> AggregationTable: ...


class AlertStore(Protocol):
    def events(self, query: EventQuery) -> EventPage: ...

    def definitions(self, query: ListingQuery) -> ListingPage: ...


class ViewStore(Protocol):
    def saved_searches(self, query: ListingQuery) -> ListingPage: ...

    def dashboards(self, query: ListingQuery) -> ListingPage: ...

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

    def lookup_tables(self, query: ListingQuery) -> ListingPage: ...

    def lookup(self, table: str, key: str) -> LookupResult: ...

    def lookup_table_exists(self, table: str) -> bool: ...
