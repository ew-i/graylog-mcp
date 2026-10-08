import unittest
from datetime import datetime, timezone

from graylog_mcp.application.alerts import AlertService
from graylog_mcp.application.configuration import ConfigurationService
from graylog_mcp.application.operations import OperationsService
from graylog_mcp.application.saved_views import SavedViewService
from graylog_mcp.application.service import LogService
from graylog_mcp.domain.alerts import Event, EventDefinition, EventPage
from graylog_mcp.domain.configuration import (
    IndexSet,
    LookupResult,
    LookupTable,
    Pipeline,
    PipelineConnection,
    PipelineRule,
    PipelineStage,
    Strategy,
    StreamConfig,
    StreamRule,
)
from graylog_mcp.domain.errors import InvalidRequestError, NotFoundError
from graylog_mcp.domain.models import Between, LastSeconds, ListingPage, ListingQuery
from graylog_mcp.domain.operations import (
    InputNodeState,
    InputStatus,
    NodeStatus,
    NodeThroughput,
    SystemNotification,
)
from graylog_mcp.domain.views import SavedQuery, ViewSummary

from .fakes import FakeAlertStore, FakeConfigStore, FakeLogStore, FakeSystemStore, FakeViewStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


class AlertServiceTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeAlertStore()
        self.service = AlertService(self.store, clock=lambda: NOW)

    def test_recent_alerts_query_and_shape(self):
        self.store.page = EventPage(
            7,
            (
                Event(
                    "e1",
                    "d1",
                    "High error rate",
                    "t",
                    "errors > 100",
                    3,
                    True,
                    "node",
                    "",
                    {"n": 1},
                ),
            ),
            1,
        )
        result = self.service.recent_alerts(
            seconds=3600, limit=5000, alerts_only=False, definition_id=" d1 "
        )
        q = self.store.queries[-1]
        self.assertEqual(
            (q.window, q.limit, q.alerts_only, q.definition_ids),
            (
                Between("2026-10-07T11:00:00.000Z", "2026-10-07T12:00:00.000Z"),
                200,
                False,
                ("d1",),
            ),
        )
        self.assertEqual(result["window"]["relative_seconds"], 3600)
        event = result["events"][0]
        self.assertEqual(
            (event["title"], event["priority"], event["key"]), ("High error rate", "high", None)
        )
        self.assertEqual(result["total"], 7)

    def test_event_cursor_keeps_window_and_filters(self):
        event = Event("e1", "d1", "t", "t", "m", 1, True, None, None)
        self.store.page = EventPage(3, (event,), 1)
        first = self.service.recent_alerts(
            seconds=600, limit=1, alerts_only=False, definition_id="d1"
        )
        self.service.recent_alerts(seconds=60, limit=50, next_cursor=first["next_cursor"])
        a, b = self.store.queries
        self.assertEqual((b.window, b.alerts_only, b.definition_ids), (a.window, False, ("d1",)))
        self.assertEqual((b.limit, b.offset), (1, 1))

        with self.assertRaisesRegex(InvalidRequestError, "definition_id"):
            self.service.recent_alerts(
                seconds=600, limit=1, definition_id="d2", next_cursor=first["next_cursor"]
            )

    def test_event_cursor_continues_at_the_stores_next_offset(self):
        event = Event("e1", "d1", "t", "t", "m", 1, True, None, None)
        self.store.page = EventPage(5, (event,), 2)  # store dropped one of two entries
        first = self.service.recent_alerts(seconds=600, limit=2)
        self.service.recent_alerts(seconds=600, limit=2, next_cursor=first["next_cursor"])
        self.assertEqual(self.store.queries[-1].offset, 2)

    def test_definitions_convert_milliseconds(self):
        self.store.definition_list = [
            EventDefinition(
                "d1", "t", None, 2, True, "aggregation-v1", "level:3", ("s1",), 300000, 60000
            )
        ]
        d = self.service.alert_definitions(text=" err ")["definitions"][0]
        self.assertEqual(
            (d["search_within_seconds"], d["execute_every_seconds"], d["priority"]),
            (300, 60, "normal"),
        )
        self.assertEqual(self.store.definition_requests[-1], ("err", 50))

    def test_definition_cursor_paginates(self):
        self.store.definition_list = [
            EventDefinition("d1", "one", None, 1, True, None, None, (), None, None),
            EventDefinition("d2", "two", None, 1, True, None, None, (), None, None),
        ]
        first = self.service.alert_definitions(limit=1)
        second = self.service.alert_definitions(limit=1, next_cursor=first["next_cursor"])
        self.assertEqual(
            [item["id"] for item in first["definitions"] + second["definitions"]], ["d1", "d2"]
        )
        self.assertFalse(second["has_more"])


class SavedViewServiceTest(unittest.TestCase):
    def setUp(self):
        self.logs = FakeLogStore(entries=[{"msg": "hit"}])
        self.views = FakeViewStore()
        self.service = SavedViewService(self.views, LogService(self.logs, clock=lambda: NOW))

    def test_listings(self):
        self.views.searches = [ViewSummary("v1", "Errors", None, None, "admin", "t")]
        self.assertEqual(self.service.saved_searches()["saved_searches"][0]["id"], "v1")
        self.assertEqual(self.service.dashboards()["total"], 0)

    def test_saved_search_cursor_paginates(self):
        self.views.searches = [
            ViewSummary("v1", "One", None, None, None, None),
            ViewSummary("v2", "Two", None, None, None, None),
        ]
        first = self.service.saved_searches(limit=1)
        second = self.service.saved_searches(limit=1, next_cursor=first["next_cursor"])
        self.assertEqual(
            [item["id"] for item in first["saved_searches"] + second["saved_searches"]],
            ["v1", "v2"],
        )
        self.assertEqual(second["next_cursor"], None)

    def test_listing_cursor_continues_at_the_stores_next_offset(self):
        # The store dropped a malformed entry from the first page of two.
        pages = {
            0: ListingPage(4, (ViewSummary("v1", "One", None, None, None, None),), 2),
            2: ListingPage(
                4,
                (
                    ViewSummary("v3", "Three", None, None, None, None),
                    ViewSummary("v4", "Four", None, None, None, None),
                ),
                4,
            ),
        }
        requested = []

        def saved_searches(query):
            requested.append(query)
            return pages[query.offset]

        self.views.saved_searches = saved_searches
        first = self.service.saved_searches(text="x", limit=2)
        second = self.service.saved_searches(limit=2, next_cursor=first["next_cursor"])
        self.assertEqual(requested, [ListingQuery(2, "x", 0), ListingQuery(2, "x", 2)])
        self.assertEqual([i["id"] for i in second["saved_searches"]], ["v3", "v4"])
        self.assertFalse(second["has_more"])

    def test_run_uses_saved_query_stream_and_window(self):
        self.views.saved["v1"] = SavedQuery(
            "v1", "Errors", "level:3", ("s1", "s2"), LastSeconds(600)
        )
        result = self.service.run_saved_search(view_id="v1")
        q = self.logs.last_query
        self.assertEqual(
            (q.text, q.stream_id, q.window),
            ("level:3", "s1", Between("2026-10-07T11:50:00.000Z", "2026-10-07T12:00:00.000Z")),
        )
        self.assertEqual(result["stream_id"], "s1")
        self.assertIn("2 streams", result["notes"][0])
        self.assertEqual(result["result"]["messages"], [{"msg": "hit"}])

    def test_run_absolute_and_override(self):
        self.views.saved["v2"] = SavedQuery("v2", "Night", "", (), Between("a", "b"))
        self.service.run_saved_search(view_id="v2", stream_id="s9")
        q = self.logs.last_query
        self.assertEqual((q.text, q.stream_id, q.window), ("*", "s9", Between("a", "b")))

    def test_resumed_run_keeps_the_chosen_stream(self):
        self.views.saved["v1"] = SavedQuery("v1", "Errors", "level:3", ("s1", "s2"), None)
        self.logs.entries = [{"msg": "a"}, {"msg": "b"}]
        first = self.service.run_saved_search(view_id="v1", stream_id="s2", limit=1)
        second = self.service.run_saved_search(
            view_id="v1", limit=1, next_cursor=first["result"]["next_cursor"]
        )
        self.assertEqual((second["stream_id"], self.logs.last_query.stream_id), ("s2", "s2"))
        self.assertEqual(second["result"]["messages"], [{"msg": "b"}])

    def test_keyword_range_falls_back(self):
        self.views.saved["v3"] = SavedQuery(
            "v3", "K", "*", ("s1",), None, keyword="last five minutes"
        )
        result = self.service.run_saved_search(view_id="v3")
        self.assertEqual(
            self.logs.last_query.window,
            Between("2026-10-07T11:45:00.000Z", "2026-10-07T12:00:00.000Z"),
        )
        self.assertIn("last five minutes", result["notes"][0])

    def test_stream_needed_when_saved_search_has_none(self):
        self.views.saved["v4"] = SavedQuery("v4", "All", "*", (), LastSeconds(60))
        with self.assertRaises(InvalidRequestError):
            self.service.run_saved_search(view_id="v4")
        with self.assertRaises(InvalidRequestError):
            self.service.run_saved_search(view_id=" ")
        with self.assertRaises(NotFoundError):
            self.service.run_saved_search(view_id="nope")


class OperationsServiceTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeSystemStore()
        self.service = OperationsService(self.store)

    def test_input_status_and_problem_filter(self):
        ok = InputStatus(
            "i1", "gelf", "GELF UDP", True, None, (InputNodeState("n1", "RUNNING", None, None),)
        )
        bad = InputStatus(
            "i2",
            "syslog",
            "Syslog",
            False,
            "n1",
            (InputNodeState("n1", "FAILED", None, "port in use"),),
        )
        self.store.input_list = [ok, bad]
        full = self.service.input_status()
        self.assertEqual((full["total"], full["problems"]), (2, 1))
        only = self.service.input_status(only_problems=True)
        self.assertEqual([i["id"] for i in only["inputs"]], ["i2"])
        self.assertEqual(only["inputs"][0]["states"][0]["message"], "port in use")

    def test_throughput_totals(self):
        self.store.rates = [
            NodeThroughput("n1", {"input_per_second": 10.5, "output_per_second": 10.0}),
            NodeThroughput("n2", {"input_per_second": 4.5, "output_per_second": None}),
        ]
        result = self.service.throughput()
        self.assertEqual(result["cluster"], {"input_per_second": 15.0, "output_per_second": 10.0})
        self.assertEqual(result["nodes"][1]["node_id"], "n2")

    def test_notifications_urgent_first_then_newest(self):
        self.store.notes = [
            SystemNotification("a", "normal", "2026-01-03", None, None, None),
            SystemNotification("b", "urgent", "2026-01-01", None, None, None),
            SystemNotification("c", "urgent", "2026-01-02", None, None, None),
        ]
        self.assertEqual(
            [n["type"] for n in self.service.notifications()["notifications"]], ["c", "b", "a"]
        )

    def test_cluster_nodes_summary(self):
        self.store.node_list = [
            NodeStatus("n1", True, is_leader=True, is_processing=True, lifecycle="running"),
            NodeStatus("n2", False),
        ]
        result = self.service.cluster_nodes()
        self.assertEqual((result["total"], result["healthy"], result["leader"]), (2, 1, "n1"))


class ConfigurationServiceTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeConfigStore()
        self.service = ConfigurationService(self.store)
        self.store.streams["s1"] = StreamConfig(
            "s1",
            "api",
            None,
            "OR",
            False,
            "is1",
            (StreamRule("source", 1, "api", False, None), StreamRule("level", 3, "4", True, None)),
        )

    def test_stream_rules(self):
        result = self.service.stream_rules(stream_id="s1")
        self.assertIn("at least one of the 2 rule(s)", result["summary"])
        self.assertEqual(result["rules"][1]["meaning"], "level must not be greater than '4'")
        with self.assertRaises(InvalidRequestError):
            self.service.stream_rules(stream_id=" ")

    def test_pipeline_rules_for_stream(self):
        self.store.pipeline_list = [
            Pipeline(
                "p1",
                "Clean",
                None,
                (
                    PipelineStage(1, "EITHER", ("drop debug", "missing rule")),
                    PipelineStage(0, "ALL", ("parse",)),
                ),
            ),
            Pipeline("p2", "Other", None, ()),
        ]
        self.store.connections = [
            PipelineConnection("s1", ("p1", "ghost")),
            PipelineConnection("s2", ("p2", "p1")),
        ]
        self.store.rules = [
            PipelineRule("r1", "drop debug", "drops debug", 'rule "drop debug" when ... end'),
            PipelineRule("r2", "parse", None, 'rule "parse" ...'),
        ]
        result = self.service.pipeline_rules(stream_id="s1")
        self.assertEqual([p["id"] for p in result["pipelines"]], ["p1", "ghost"])
        clean = result["pipelines"][0]
        self.assertEqual(clean["connected_streams"], ["s1", "s2"])
        self.assertEqual([s["stage"] for s in clean["stages"]], [0, 1])
        stage1 = clean["stages"][1]["rules"]
        self.assertTrue(stage1[0]["found"] and "source" in stage1[0])
        self.assertFalse(stage1[1]["found"])
        self.assertTrue(result["pipelines"][1]["missing"])

        no_source = self.service.pipeline_rules(stream_id="s1", include_source=False)
        self.assertNotIn("source", no_source["pipelines"][0]["stages"][0]["rules"][0])
        self.assertEqual(self.service.pipeline_rules()["total"], 2)

    def test_index_sets_mark_stream_usage(self):
        strategy = Strategy("DeletionRetentionStrategy", {"max_number_of_indices": 20})
        self.store.sets = [
            IndexSet("is0", "Default", None, "graylog", True, True, 1, 0, strategy, strategy),
            IndexSet("is1", "API", None, "api", False, True, 1, 0, strategy, strategy),
        ]
        result = self.service.index_sets(stream_id="s1")
        self.assertEqual(result["index_sets"][0]["id"], "is1")
        self.assertTrue(result["index_sets"][0]["used_by_stream"])
        self.assertIsNone(self.service.index_sets()["index_sets"][0]["used_by_stream"])

    def test_lookup(self):
        self.store.lookups[("hosts", "10.0.0.1")] = LookupResult(
            "hosts", "10.0.0.1", "db-1", None, None, False, 60
        )
        self.assertEqual(
            self.service.lookup_value(table="hosts", key="10.0.0.1")["single_value"], "db-1"
        )
        with self.assertRaises(InvalidRequestError):
            self.service.lookup_value(table="hosts", key="")

    def test_lookup_tables(self):
        self.store.tables = [LookupTable("t1", "hosts", "Hosts", None)]

        result = self.service.lookup_tables(text=" hosts ", limit=5000)

        self.assertEqual(result["lookup_tables"][0]["name"], "hosts")
        self.assertEqual(result["total"], 1)


if __name__ == "__main__":
    unittest.main()
