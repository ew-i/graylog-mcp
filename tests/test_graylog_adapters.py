"""Adapters for the new areas, exercised through httpx.MockTransport."""

import json
import unittest

import httpx

from graylog_mcp.domain.aggregation import AggregationQuery, Metric, TimeGrouping, ValuesGrouping
from graylog_mcp.domain.alerts import EventQuery
from graylog_mcp.domain.errors import (
    BackendError,
    InvalidRequestError,
    NotFoundError,
    UnsupportedError,
)
from graylog_mcp.domain.models import Between, LastSeconds, ListingQuery
from graylog_mcp.infrastructure.graylog_alerts import GraylogAlertStore
from graylog_mcp.infrastructure.graylog_analytics import GraylogAnalyticsStore
from graylog_mcp.infrastructure.graylog_config import GraylogConfigStore
from graylog_mcp.infrastructure.graylog_system import GraylogSystemStore
from graylog_mcp.infrastructure.graylog_views import GraylogViewStore


class Router:
    """Answers (METHOD, path) with JSON bodies; unknown routes are 404. Records requests."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.routes.get((request.method, request.url.path))
        if reply is None:
            return httpx.Response(404, text="not found")
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def body(self, index=-1):
        return json.loads(self.requests[index].content)


def make(store_type, routes):
    router = Router(routes)
    client = httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(router))
    return store_type(client), router


class AnalyticsAdapterTest(unittest.TestCase):
    def test_given_grouped_query_when_aggregating_then_sends_body_and_maps_rows(self):
        # Given
        store, router = make(
            GraylogAnalyticsStore,
            {
                ("POST", "/api/search/aggregate"): {
                    "schema": [{}, {}, {}],
                    "datarows": [
                        ["2026-09-25T10:00:00.000Z", "api", 4],
                        ["2026-09-25T10:00:00.000Z", None, 1],
                    ],
                }
            },
        )

        # When
        result = store.aggregate(
            AggregationQuery(
                "level:3",
                "s1",
                LastSeconds(3600),
                (TimeGrouping("1h"), ValuesGrouping("source", 5)),
                (Metric("count", descending=True),),
            )
        )

        # Then
        self.assertEqual(
            router.body(),
            {
                "query": "level:3",
                "streams": ["s1"],
                "timerange": {"type": "relative", "range": 3600},
                "group_by": [
                    {"field": "timestamp", "timeunit": "1h"},
                    {"field": "source", "limit": 5},
                ],
                "metrics": [{"function": "count", "sort": "desc"}],
            },
        )
        self.assertEqual(result.rows[0].keys, ("2026-09-25T10:00:00.000Z", "api"))
        self.assertEqual(result.rows[0].values, {"count": 4})
        self.assertEqual(result.rows[1].keys[1], None)

    def test_given_unknown_grouping_type_when_aggregating_then_raises_type_error(self):
        # Given
        store, _ = make(GraylogAnalyticsStore, {})

        # When / Then
        with self.assertRaises(TypeError):
            store.aggregate(AggregationQuery("*", "s1", LastSeconds(60), (object(),), (Metric("count"),)))

    def test_given_ungrouped_query_when_aggregating_then_picks_the_streams_row_by_document_count(self):
        # Given
        # Graylog 6.3 labels rows with stream titles, 5.1 with IDs; the count decides.
        store, router = make(
            GraylogAnalyticsStore,
            {
                ("POST", "/api/search/aggregate"): {
                    "datarows": [["Other stream", 3, 10.0, 3], ["Default Stream", 9, 12.5, 9]],
                }
            },
        )

        # When
        result = store.aggregate(
            AggregationQuery(
                "*",
                "s1",
                Between("a", "b"),
                (),
                (Metric("count", "t"), Metric("percentile", "t", percentile=90)),
            )
        )
        body = router.body()

        # Then
        self.assertEqual(body["group_by"], [{"field": "streams", "limit": 100}])
        self.assertEqual(body["timerange"], {"type": "absolute", "from": "a", "to": "b"})
        self.assertEqual(
            body["metrics"][1],
            {"function": "percentile", "field": "t", "configuration": {"percentile": 90}},
        )
        self.assertEqual(body["metrics"][2], {"function": "count"})  # hidden document count
        self.assertEqual(len(result.rows), 1)
        self.assertEqual((result.rows[0].keys, result.rows[0].values), ((), {"count(t)": 9, "p90(t)": 12.5}))

    def test_given_plain_count_or_no_rows_when_aggregating_ungrouped_then_reuses_the_plain_count_and_returns_no_rows(self):
        # Given
        store, router = make(
            GraylogAnalyticsStore,
            {("POST", "/api/search/aggregate"): {"datarows": [["a", 2], ["b", 5]]}},
        )
        query = AggregationQuery("*", "s1", LastSeconds(60), (), (Metric("count"),))

        # When / Then
        self.assertEqual(store.aggregate(query).rows[0].values, {"count": 5})
        self.assertEqual(router.body()["metrics"], [{"function": "count"}])

        # Given
        store, _ = make(GraylogAnalyticsStore, {("POST", "/api/search/aggregate"): {}})

        # When / Then
        self.assertEqual(store.aggregate(query).rows, ())

    def test_given_full_text_field_rejection_when_aggregating_then_passes_the_backend_error_through(self):
        # Given: the analytics service names the field from field types, whatever the wording.
        reply = '{"message":"search_phase_execution_exception, reason=all shards failed"}'
        store, _ = make(
            GraylogAnalyticsStore,
            {("POST", "/api/search/aggregate"): httpx.Response(400, text=reply)},
        )
        query = AggregationQuery("*", "s1", LastSeconds(60), (ValuesGrouping("message", 5),), (Metric("count"),))

        # When
        with self.assertRaises(BackendError) as raised:
            store.aggregate(query)

        # Then
        self.assertRegex(str(raised.exception), "all shards failed")

    def test_given_no_time_grouping_on_5_1_when_aggregating_then_raises_unsupported(self):
        # Given
        reply = '{"message":"Unable to map property timeunit. Known properties: field, limit"}'
        store, _ = make(
            GraylogAnalyticsStore,
            {("POST", "/api/search/aggregate"): httpx.Response(400, text=reply)},
        )
        query = AggregationQuery("*", "s1", LastSeconds(60), (TimeGrouping("5m"),), (Metric("count"),))

        # When
        with self.assertRaises(UnsupportedError) as raised:
            store.aggregate(query)

        # Then
        self.assertRegex(str(raised.exception), "cannot group by time")

    def test_given_other_aggregation_error_when_aggregating_then_raises_backend_error(self):
        # Given
        store, _ = make(
            GraylogAnalyticsStore,
            {("POST", "/api/search/aggregate"): httpx.Response(400, text="query_parsing failed")},
        )

        # When
        with self.assertRaises(BackendError) as ctx:
            store.aggregate(AggregationQuery("*", "s1", LastSeconds(60), (), (Metric("count"),)))

        # Then
        self.assertNotIsInstance(ctx.exception, InvalidRequestError)

    def test_given_no_aggregate_endpoint_when_aggregating_then_raises_unsupported_naming_5_1(self):
        # Given
        store, _ = make(GraylogAnalyticsStore, {})

        # When
        with self.assertRaises(UnsupportedError) as ctx:
            store.aggregate(AggregationQuery("*", "s1", LastSeconds(60), (), (Metric("count"),)))

        # Then
        self.assertIn("5.1", str(ctx.exception))

    def test_given_malformed_rows_when_aggregating_then_raises_backend_error(self):
        # Given
        store, _ = make(
            GraylogAnalyticsStore,
            {("POST", "/api/search/aggregate"): {"datarows": [["only-one"]]}},
        )

        # When / Then
        with self.assertRaises(BackendError):
            store.aggregate(AggregationQuery("*", "s1", LastSeconds(60), (ValuesGrouping("x", 1),), (Metric("count"),)))


class AlertAdapterTest(unittest.TestCase):
    def test_given_events_reply_when_searching_events_then_sends_body_and_maps_events(self):
        # Given
        store, router = make(
            GraylogAlertStore,
            {
                ("POST", "/api/events/search"): {
                    "events": [
                        {
                            "event": {
                                "id": "e1",
                                "event_definition_id": "d1",
                                "timestamp": "t1",
                                "message": "boom",
                                "priority": 3,
                                "alert": True,
                                "source": "node-1",
                                "key": "",
                                "fields": {"n": 2},
                            },
                            "index_name": "gl-events_0",
                        },
                        {"id": "e2", "event_definition_id": "d9", "alert": False},
                    ],
                    "total_events": 12,
                    "context": {"event_definitions": {"d1": {"id": "d1", "title": "Errors spiking"}}},
                }
            },
        )

        # When
        page = store.events(EventQuery(window=LastSeconds(600), limit=20, text="db", definition_ids=("d1",)))

        # Then
        self.assertEqual(
            router.body(),
            {
                "query": "db",
                "page": 1,
                "per_page": 20,
                "filter": {"alerts": "only", "event_definitions": ["d1"]},
                "timerange": {"type": "relative", "range": 600},
                "sort_by": "timestamp",
                "sort_direction": "desc",
            },
        )
        self.assertEqual((page.total, page.next_offset), (12, 20))
        first, second = page.events
        self.assertEqual((first.definition_title, first.priority, first.fields), ("Errors spiking", 3, {"n": 2}))
        self.assertEqual((second.id, second.definition_title, second.alert), ("e2", None, False))

    def test_given_non_alerts_wanted_when_searching_events_then_filter_includes_them(self):
        # Given
        store, router = make(GraylogAlertStore, {("POST", "/api/events/search"): {"events": []}})

        # When
        store.events(EventQuery(window=LastSeconds(60), limit=1, alerts_only=False))

        # Then
        self.assertEqual(router.body()["filter"], {"alerts": "include"})

    def test_given_malformed_definition_context_when_searching_events_then_event_has_no_title(self):
        # Given
        store, _ = make(
            GraylogAlertStore,
            {
                ("POST", "/api/events/search"): {
                    "events": [{"id": "e1", "event_definition_id": "d1"}],
                    "context": {"event_definitions": [{"id": "d1", "title": "Errors"}]},
                }
            },
        )

        # When
        page = store.events(EventQuery(window=LastSeconds(60), limit=1))

        # Then
        self.assertEqual(page.events[0].definition_title, None)

    def test_given_either_list_key_when_listing_definitions_then_parses_definition_and_pages_by_number(self):
        # Given
        definition = {
            "id": "d1",
            "title": "Errors",
            "priority": 2,
            "alert": True,
            "config": {
                "type": "aggregation-v1",
                "query": "level:3",
                "streams": ["s1"],
                "search_within_ms": 300000,
                "execute_every_ms": 60000,
            },
        }
        for key in ("event_definitions", "elements"):
            with self.subTest(key=key):
                store, router = make(GraylogAlertStore, {("GET", "/api/events/definitions"): {key: [definition]}})

                # When
                page = store.definitions(ListingQuery(10, "err", offset=10))

                # Then
                [d] = page.items
                self.assertEqual(page.next_offset, 20)
                self.assertEqual((d.kind, d.stream_ids, d.search_within_ms), ("aggregation-v1", ("s1",), 300000))
                self.assertEqual(
                    dict(router.requests[-1].url.params),
                    {"page": "2", "per_page": "10", "query": "err"},
                )


class ViewAdapterTest(unittest.TestCase):
    def test_given_view_listings_when_listing_then_returns_summaries_and_sends_query_only_when_given(self):
        # Given
        store, router = make(
            GraylogViewStore,
            {
                ("GET", "/api/search/saved"): {
                    "elements": [{"id": "v1", "title": "Errors", "owner": "admin", "last_updated_at": "t"}]
                },
                ("GET", "/api/dashboards"): {"views": [{"id": "d1", "title": "Ops"}]},
            },
        )

        # When
        [saved] = store.saved_searches(ListingQuery(5, "err")).items

        # Then
        self.assertEqual((saved.id, saved.owner, saved.last_updated), ("v1", "admin", "t"))
        self.assertEqual(router.requests[-1].url.params["query"], "err")
        self.assertEqual(store.dashboards(ListingQuery(5)).items[0].title, "Ops")
        self.assertNotIn("query", router.requests[-1].url.params)

    def test_given_saved_search_when_reading_query_then_returns_text_streams_and_window(self):
        # Given
        search = {
            "queries": [
                {
                    "query": {"type": "elasticsearch", "query_string": "level:3"},
                    "timerange": {"type": "relative", "range": 300},
                    "filter": {
                        "type": "or",
                        "filters": [
                            {"type": "stream", "id": "s1"},
                            {"type": "stream", "id": "s2"},
                        ],
                    },
                }
            ]
        }
        store, _ = make(
            GraylogViewStore,
            {
                ("GET", "/api/views/v1"): {"id": "v1", "title": "Errors", "search_id": "q1"},
                ("GET", "/api/views/search/q1"): search,
            },
        )

        # When
        saved = store.saved_query("v1")

        # Then
        self.assertEqual(
            (saved.query_text, saved.stream_ids, saved.window),
            ("level:3", ("s1", "s2"), LastSeconds(300)),
        )

    def test_given_time_range_variants_when_reading_saved_query_then_returns_window_or_keyword(self):
        # Given
        cases = [
            ({"type": "relative", "from": 900}, LastSeconds(900), None),
            ({"type": "absolute", "from": "a", "to": "b"}, Between("a", "b"), None),
            ({"type": "keyword", "keyword": "yesterday"}, None, "yesterday"),
            ({"type": "relative", "range": 0}, None, "all time"),
        ]
        for timerange, window, keyword in cases:
            with self.subTest(timerange=timerange):
                store, _ = make(
                    GraylogViewStore,
                    {
                        ("GET", "/api/views/v1"): {"search_id": "q1"},
                        ("GET", "/api/views/search/q1"): {"queries": [{"timerange": timerange}]},
                    },
                )

                # When
                saved = store.saved_query("v1")

                # Then
                self.assertEqual((saved.window, saved.keyword), (window, keyword))

        # Given
        store, _ = make(
            GraylogViewStore,
            {
                ("GET", "/api/views/v1"): {"search_id": "q1"},
                ("GET", "/api/views/search/q1"): {"queries": [{"timerange": {"type": "other"}}]},
            },
        )

        # When
        saved = store.saved_query("v1")

        # Then
        self.assertEqual((saved.window, saved.keyword), (None, "other"))

    def test_given_odd_timerange_and_filter_when_reading_saved_query_then_returns_no_streams_window_or_keyword(self):
        # Given
        store, _ = make(
            GraylogViewStore,
            {
                ("GET", "/api/views/v1"): {"search_id": "q1"},
                ("GET", "/api/views/search/q1"): {"queries": [{"filter": ["not a tree"], "timerange": "all"}]},
            },
        )

        # When
        saved = store.saved_query("v1")

        # Then
        self.assertEqual((saved.stream_ids, saved.window, saved.keyword), ((), None, None))

    def test_given_view_without_search_when_reading_saved_query_then_rejects_it(self):
        # Given
        store, _ = make(GraylogViewStore, {("GET", "/api/views/v1"): {}})

        # When / Then
        with self.assertRaises(InvalidRequestError):
            store.saved_query("v1")


class SystemAdapterTest(unittest.TestCase):
    def test_given_cluster_input_states_when_reading_inputs_then_merges_states_per_node(self):
        # Given
        store, _ = make(
            GraylogSystemStore,
            {
                ("GET", "/api/system/inputs"): {
                    "inputs": [
                        {"id": "i1", "title": "gelf", "name": "GELF UDP", "global": True},
                        {
                            "id": "i2",
                            "title": "idle",
                            "name": "Syslog",
                            "global": False,
                            "node": "n1",
                        },
                    ]
                },
                ("GET", "/api/cluster/inputstates"): {
                    "n1": [{"id": "i1", "state": "RUNNING", "started_at": "t"}],
                    "n2": [
                        {
                            "message_input": {"id": "i1"},
                            "state": "FAILED",
                            "detailed_message": "bind failed",
                        }
                    ],
                },
            },
        )

        # When
        first, second = store.inputs()

        # Then
        self.assertEqual([(s.node_id, s.state) for s in first.states], [("n1", "RUNNING"), ("n2", "FAILED")])
        self.assertEqual(first.health, "problem")
        self.assertEqual((second.health, second.node_id), ("unknown", "n1"))

    def test_given_no_cluster_states_when_reading_inputs_then_uses_local_states(self):
        # Given
        store, _ = make(
            GraylogSystemStore,
            {
                ("GET", "/api/system/inputs"): {"inputs": [{"id": "i1"}]},
                ("GET", "/api/system/inputstates"): {"states": [{"id": "i1", "state": "RUNNING"}]},
            },
        )

        # When / Then
        self.assertEqual(store.inputs()[0].states[0].node_id, "local")

    def test_given_odd_states_and_metrics_when_reading_then_skips_state_without_id_and_parses_nested_metric(self):
        # Given
        store, _ = make(
            GraylogSystemStore,
            {
                ("GET", "/api/system/inputs"): {"inputs": [{"id": "i1"}]},
                ("GET", "/api/cluster/inputstates"): {"n1": [{"state": "UNKNOWN"}]},
                ("POST", "/api/cluster/metrics/multiple"): {
                    "n1": {
                        "metrics": [
                            {
                                "full_name": "org.graylog2.throughput.input.1-sec-rate",
                                "metric": {"rate": {"one_minute": 4}},
                            },
                            {
                                "full_name": "org.graylog2.throughput.output.1-sec-rate",
                                "metric": 2,
                            },
                        ]
                    }
                },
            },
        )

        # When / Then
        self.assertEqual(store.inputs()[0].states, ())
        self.assertEqual(store.throughput()[0].metrics["input_per_second"], 4)

    def test_given_cluster_or_local_metrics_when_reading_throughput_then_maps_cluster_and_local_metrics(self):
        # Given
        metrics = {
            "metrics": [
                {"full_name": "org.graylog2.throughput.input.1-sec-rate", "metric": {"value": 42}},
                {"full_name": "org.graylog2.journal.entries-uncommitted", "metric": {"value": 7}},
            ]
        }
        store, router = make(GraylogSystemStore, {("POST", "/api/cluster/metrics/multiple"): {"n1": metrics}})

        # When
        [node] = store.throughput()

        # Then
        self.assertEqual(
            (
                node.node_id,
                node.metrics["input_per_second"],
                node.metrics["journal_uncommitted_entries"],
            ),
            ("n1", 42, 7),
        )
        self.assertIsNone(node.metrics["output_per_second"])
        self.assertIn("org.graylog2.buffers.process.usage", router.body()["metrics"])

        # Given
        store, _ = make(GraylogSystemStore, {("POST", "/api/system/metrics/multiple"): metrics})

        # When / Then
        self.assertEqual(store.throughput()[0].node_id, "local")

    def test_given_notification_when_listing_then_keeps_severity_and_details(self):
        # Given
        store, _ = make(
            GraylogSystemStore,
            {
                ("GET", "/api/system/notifications"): {
                    "notifications": [
                        {
                            "type": "journal_utilization_too_high",
                            "severity": "urgent",
                            "timestamp": "t",
                            "node_id": "n1",
                            "details": {"journal_utilization_percentage": 95},
                        },
                    ]
                }
            },
        )

        # When
        [note] = store.notifications()

        # Then
        self.assertEqual((note.severity, note.details["journal_utilization_percentage"]), ("urgent", 95))

    def test_given_overview_and_registry_when_listing_nodes_then_merges_overview_and_registry(self):
        # Given
        store, _ = make(
            GraylogSystemStore,
            {
                ("GET", "/api/cluster"): {
                    "n1": {
                        "hostname": "gl-1",
                        "version": "6.1",
                        "is_processing": True,
                        "lifecycle": "RUNNING",
                    },
                    "n2": None,
                },
                ("GET", "/api/system/cluster/nodes"): {
                    "nodes": [
                        {
                            "node_id": "n1",
                            "is_leader": True,
                            "transport_address": "http://gl-1:9000/",
                        },
                        {"node_id": "n2", "is_master": False, "hostname": "gl-2"},
                    ]
                },
            },
        )

        # When
        n1, n2 = store.nodes()

        # Then
        self.assertEqual((n1.is_leader, n1.transport_address, n1.healthy), (True, "http://gl-1:9000/", True))
        self.assertEqual((n2.reachable, n2.hostname, n2.is_leader, n2.healthy), (False, "gl-2", False, False))


ROTATION_CLASS = "org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategy"
RETENTION_CLASS = "org.graylog2.indexer.retention.strategies.DeletionRetentionStrategy"


class ConfigAdapterTest(unittest.TestCase):
    def test_given_stream_with_rules_when_reading_then_maps_rules_and_escapes_the_path(self):
        # Given
        store, router = make(
            GraylogConfigStore,
            {
                ("GET", "/api/streams/s/1"): {  # router matches the decoded path
                    "id": "s/1",
                    "title": "API",
                    "matching_type": "AND",
                    "index_set_id": "is1",
                    "rules": [{"field": "source", "type": 1, "value": "api", "inverted": False}],
                }
            },
        )

        # When
        stream = store.stream("s/1")

        # Then
        self.assertEqual(
            (stream.matching_type, stream.index_set_id, stream.rules[0].type_name),
            ("AND", "is1", "exact"),
        )
        self.assertEqual(router.requests[-1].url.raw_path, b"/api/streams/s%2F1")

    def test_given_pipeline_endpoints_when_reading_then_maps_stages_connections_and_rules(self):
        # Given
        store, _ = make(
            GraylogConfigStore,
            {
                ("GET", "/api/system/pipelines/pipeline"): [
                    {
                        "id": "p1",
                        "title": "Clean",
                        "stages": [
                            {"stage": 0, "match": "ALL", "rules": ["a"]},
                            {"stage": 1, "match_all": False, "rules": ["b"]},
                        ],
                    },
                ],
                ("GET", "/api/system/pipelines/connections"): [{"id": "c1", "stream_id": "s1", "pipeline_ids": ["p1"]}],
                ("GET", "/api/system/pipelines/rule"): [{"id": "r1", "title": "a", "source": 'rule "a" ...'}],
            },
        )

        # When
        [pipeline] = store.pipelines()

        # Then
        self.assertEqual(
            [(s.stage, s.match, s.rules) for s in pipeline.stages],
            [(0, "ALL", ("a",)), (1, "EITHER", ("b",))],
        )
        self.assertEqual(store.pipeline_connections()[0].pipeline_ids, ("p1",))
        self.assertEqual(store.pipeline_rules()[0].source, 'rule "a" ...')

    def test_given_object_where_list_expected_when_reading_pipelines_then_raises_backend_error(self):
        # Given
        store, _ = make(GraylogConfigStore, {("GET", "/api/system/pipelines/pipeline"): {"oops": True}})

        # When / Then
        with self.assertRaises(BackendError):
            store.pipelines()

    def test_given_index_set_when_reading_then_simplifies_the_strategies(self):
        # Given
        store, router = make(
            GraylogConfigStore,
            {
                ("GET", "/api/system/indices/index_sets"): {
                    "index_sets": [
                        {
                            "id": "is1",
                            "title": "API",
                            "default": False,
                            "writable": True,
                            "shards": 2,
                            "replicas": 1,
                            "rotation_strategy_class": ROTATION_CLASS,
                            "rotation_strategy": {
                                "type": "org.graylog2...TimeBasedRotationStrategyConfig",
                                "rotation_period": "P1D",
                            },
                            "retention_strategy_class": RETENTION_CLASS,
                            "retention_strategy": {"type": "x", "max_number_of_indices": 30},
                            "data_tiering": {"index_lifetime_min": "P30D"},
                        }
                    ]
                }
            },
        )

        # When
        [s] = store.index_sets()

        # Then
        self.assertEqual(s.rotation.name, "TimeBasedRotationStrategy")
        self.assertEqual(s.rotation.config, {"rotation_period": "P1D"})
        self.assertEqual(s.retention.config, {"max_number_of_indices": 30})
        self.assertEqual(s.data_tiering, {"index_lifetime_min": "P30D"})
        self.assertEqual(router.requests[-1].url.params["stats"], "false")

    def test_given_lookup_table_when_listing_and_looking_up_then_returns_the_value(self):
        # Given
        store, router = make(
            GraylogConfigStore,
            {
                ("GET", "/api/system/lookup/tables"): {"lookup_tables": [{"id": "t1", "name": "hosts", "title": "Hosts"}]},
                ("GET", "/api/system/lookup/tables/hosts/query"): {
                    "single_value": "db-1",
                    "has_error": False,
                    "ttl": 60,
                },
            },
        )

        # When / Then
        self.assertEqual(store.lookup_tables(ListingQuery(10, "hosts")).items[0].name, "hosts")
        result = store.lookup("hosts", "10.0.0.1")
        self.assertEqual((result.single_value, result.ttl), ("db-1", 60))
        self.assertEqual(router.requests[-1].url.params["key"], "10.0.0.1")

    def test_given_dropped_entry_when_paging_by_number_then_moves_next_offset_a_whole_page_on(self):
        # Given
        store, router = make(
            GraylogConfigStore,
            {
                ("GET", "/api/system/lookup/tables"): {
                    "lookup_tables": [{"id": "t5", "name": "hosts"}, {"id": "t6"}],
                    "total": 8,
                }
            },
        )

        # When
        page = store.lookup_tables(ListingQuery(2, offset=4))

        # Then
        self.assertEqual(router.requests[-1].url.params["page"], "3")
        self.assertEqual([t.name for t in page.items], ["hosts"])  # nameless table dropped
        self.assertEqual(page.next_offset, 6)

    def test_given_no_filter_or_stage_match_when_reading_then_omits_the_query_and_leaves_match_empty(self):
        # Given
        store, router = make(
            GraylogConfigStore,
            {
                ("GET", "/api/system/lookup/tables"): {"lookup_tables": []},
                ("GET", "/api/system/pipelines/pipeline"): [{"id": "p1", "stages": [{"stage": 1, "rules": []}]}],
            },
        )

        # When / Then
        self.assertEqual(store.lookup_tables(ListingQuery(5)).items, ())
        self.assertNotIn("query", router.requests[-1].url.params)
        self.assertIsNone(store.pipelines()[0].stages[0].match)

    def test_given_unknown_table_when_looking_up_then_raises_not_found(self):
        # Given
        store, _ = make(GraylogConfigStore, {})

        # When / Then
        with self.assertRaises(NotFoundError):
            store.lookup("nope", "k")

    def test_given_known_and_unknown_tables_when_checking_existence_then_answers_true_or_false(self):
        # Given
        store, router = make(GraylogConfigStore, {("GET", "/api/system/lookup/tables/hosts"): {"name": "hosts"}})

        # When / Then
        self.assertTrue(store.lookup_table_exists("hosts"))
        self.assertFalse(store.lookup_table_exists("no such"))
        self.assertEqual(router.requests[-1].url.raw_path, b"/api/system/lookup/tables/no%20such")


if __name__ == "__main__":
    unittest.main()
