"""In-memory test doubles for the application's ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from graylog_mcp.domain.aggregation import AggregationQuery, AggregationTable
from graylog_mcp.domain.alerts import EventDefinition, EventPage, EventQuery
from graylog_mcp.domain.configuration import (
    IndexSet,
    LookupResult,
    LookupTable,
    Pipeline,
    PipelineConnection,
    PipelineRule,
    StreamConfig,
)
from graylog_mcp.domain.errors import NotFoundError
from graylog_mcp.domain.models import (
    ClusterInfo,
    ListingPage,
    ListingQuery,
    LogEntry,
    LogQuery,
    SearchPage,
    Stream,
)
from graylog_mcp.domain.operations import (
    InputStatus,
    NodeStatus,
    NodeThroughput,
    SystemNotification,
)
from graylog_mcp.domain.views import SavedQuery, ViewSummary


def _listing(items: list, query: ListingQuery) -> ListingPage:
    """An offset-paged slice, as every fake store serves listings."""
    page = tuple(items[query.offset : query.offset + query.limit])
    return ListingPage(total=len(items), items=page, next_offset=query.offset + len(page))


@dataclass
class FakeLogStore:
    info: ClusterInfo = field(
        default_factory=lambda: ClusterInfo("6.1.0", "Noir", "c1", "n1", "gl-host", True, "UTC")
    )
    stream_list: list[Stream] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    total_hits: int | None = None
    stored: dict[tuple[str, str], dict] = field(default_factory=dict)
    queries: list[LogQuery] = field(default_factory=list)
    responder: Callable[[LogQuery], SearchPage] | None = None

    def cluster_info(self) -> ClusterInfo:
        return self.info

    def streams(self) -> list[Stream]:
        return list(self.stream_list)

    def search(self, query: LogQuery) -> SearchPage:
        self.queries.append(query)
        if self.responder:
            return self.responder(query)
        hits = tuple(
            LogEntry(e) for e in self.entries[query.offset : query.offset + query.max_results]
        )
        total = self.total_hits if self.total_hits is not None else len(self.entries)
        return SearchPage(total_hits=total, entries=hits, next_offset=query.offset + len(hits))

    def fetch(self, index: str, message_id: str) -> LogEntry:
        try:
            return LogEntry(self.stored[(index, message_id)])
        except KeyError:
            raise NotFoundError(f"{index}/{message_id}") from None

    @property
    def last_query(self) -> LogQuery:
        return self.queries[-1]


@dataclass
class FakeAggregationStore:
    tables: list[AggregationTable] = field(
        default_factory=list
    )  # returned in order, last one repeats
    queries: list[AggregationQuery] = field(default_factory=list)

    def aggregate(self, query: AggregationQuery) -> AggregationTable:
        self.queries.append(query)
        if not self.tables:
            return AggregationTable(rows=())
        return self.tables[min(len(self.queries), len(self.tables)) - 1]


@dataclass
class FakeAlertStore:
    page: EventPage = field(default_factory=lambda: EventPage(0, (), 0))
    definition_list: list[EventDefinition] = field(default_factory=list)
    queries: list[EventQuery] = field(default_factory=list)
    definition_requests: list[tuple[str, int]] = field(default_factory=list)

    def events(self, query: EventQuery) -> EventPage:
        self.queries.append(query)
        return self.page

    def definitions(self, query: ListingQuery) -> ListingPage:
        self.definition_requests.append((query.text, query.limit))
        return _listing(self.definition_list, query)


@dataclass
class FakeViewStore:
    searches: list[ViewSummary] = field(default_factory=list)
    boards: list[ViewSummary] = field(default_factory=list)
    saved: dict[str, SavedQuery] = field(default_factory=dict)

    def saved_searches(self, query: ListingQuery) -> ListingPage:
        return _listing(self.searches, query)

    def dashboards(self, query: ListingQuery) -> ListingPage:
        return _listing(self.boards, query)

    def saved_query(self, view_id: str) -> SavedQuery:
        try:
            return self.saved[view_id]
        except KeyError:
            raise NotFoundError(view_id) from None


@dataclass
class FakeSystemStore:
    input_list: list[InputStatus] = field(default_factory=list)
    rates: list[NodeThroughput] = field(default_factory=list)
    notes: list[SystemNotification] = field(default_factory=list)
    node_list: list[NodeStatus] = field(default_factory=list)

    def inputs(self) -> list[InputStatus]:
        return list(self.input_list)

    def throughput(self) -> list[NodeThroughput]:
        return list(self.rates)

    def notifications(self) -> list[SystemNotification]:
        return list(self.notes)

    def nodes(self) -> list[NodeStatus]:
        return list(self.node_list)


@dataclass
class FakeConfigStore:
    streams: dict[str, StreamConfig] = field(default_factory=dict)
    pipeline_list: list[Pipeline] = field(default_factory=list)
    connections: list[PipelineConnection] = field(default_factory=list)
    rules: list[PipelineRule] = field(default_factory=list)
    sets: list[IndexSet] = field(default_factory=list)
    tables: list[LookupTable] = field(default_factory=list)
    lookups: dict[tuple[str, str], LookupResult] = field(default_factory=dict)

    def stream(self, stream_id: str) -> StreamConfig:
        try:
            return self.streams[stream_id]
        except KeyError:
            raise NotFoundError(stream_id) from None

    def pipelines(self) -> list[Pipeline]:
        return list(self.pipeline_list)

    def pipeline_connections(self) -> list[PipelineConnection]:
        return list(self.connections)

    def pipeline_rules(self) -> list[PipelineRule]:
        return list(self.rules)

    def index_sets(self) -> list[IndexSet]:
        return list(self.sets)

    def lookup_tables(self, query: ListingQuery) -> ListingPage:
        return _listing(self.tables, query)

    def lookup(self, table: str, key: str) -> LookupResult:
        try:
            return self.lookups[(table, key)]
        except KeyError:
            raise NotFoundError(f"{table}/{key}") from None
