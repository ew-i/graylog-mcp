import unittest
from datetime import datetime, timezone

from graylog_mcp.application.analytics import AnalyticsService
from graylog_mcp.application.policy import Limits
from graylog_mcp.domain.aggregation import (
    AggregationRow,
    AggregationTable,
    TimeGrouping,
    ValuesGrouping,
)
from graylog_mcp.domain.errors import BackendError, InvalidRequestError
from graylog_mcp.domain.models import Between, LastSeconds, LogEntry, SearchPage, SortDirection

from .fakes import FakeAggregationStore, FakeLogStore

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def table(*rows):
    return AggregationTable(tuple(AggregationRow(tuple(keys), values) for keys, values in rows))


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self):
        self.logs = FakeLogStore()
        self.agg = FakeAggregationStore()
        self.service = AnalyticsService(self.logs, self.agg, clock=lambda: NOW)


class CountMatchesTest(AnalyticsTestCase):
    def test_uses_single_result_search(self):
        self.logs.total_hits = 1234
        result = self.service.count_matches(text="level:3", stream_id="s1", seconds=600)
        self.assertEqual(result["total_results"], 1234)
        self.assertEqual(self.logs.last_query.max_results, 1)
        self.assertEqual(self.logs.last_query.window, LastSeconds(600))


class CountOverTimeTest(AnalyticsTestCase):
    def test_auto_interval_and_shaping(self):
        self.agg.tables = [
            table(
                (["2026-09-25T11:00:00.000Z"], {"count": 3}),
                (["2026-09-25T10:00:00.000Z"], {"count": 7}),
            )
        ]
        result = self.service.count_over_time(text="*", stream_id="s1", seconds=86400)
        query = self.agg.queries[-1]
        self.assertEqual(query.groups, (TimeGrouping("30m"),))
        self.assertEqual(result["interval"], "30m")
        self.assertEqual(
            [b["time"] for b in result["buckets"]],
            ["2026-09-25T10:00:00.000Z", "2026-09-25T11:00:00.000Z"],
        )
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["peak"], {"time": "2026-09-25T10:00:00.000Z", "count": 7})

    def test_split_by_field(self):
        self.agg.tables = [
            table(
                (["t1", "api"], {"count": 2}),
                (["t1", "web"], {"count": 1}),
                (["t2", "api"], {"count": 5}),
            )
        ]
        result = self.service.count_over_time(
            text="*", stream_id="s1", seconds=3600, interval="30m", split_by="source"
        )
        self.assertEqual(
            self.agg.queries[-1].groups, (TimeGrouping("30m"), ValuesGrouping("source", 5))
        )
        self.assertEqual(
            result["buckets"][0], {"time": "t1", "total": 3, "counts": {"api": 2, "web": 1}}
        )
        self.assertEqual(result["peak"], {"time": "t2", "count": 5})

    def test_too_many_buckets_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self.service.count_over_time(text="*", stream_id="s1", seconds=86400, interval="1m")
        self.assertEqual(self.agg.queries, [])

    def test_empty_result(self):
        result = self.service.count_over_time(text="*", stream_id="s1", seconds=3600)
        self.assertEqual((result["total"], result["peak"], result["buckets"]), (0, None, []))


class ExactCountsTest(AnalyticsTestCase):
    def test_counts_sorted_and_clamped(self):
        self.agg.tables = [table((["b"], {"count": 1}), (["a"], {"count": 9}))]
        result = self.service.exact_field_counts(
            text="*", stream_id="s1", field="pod", seconds=60, top=10_000
        )
        self.assertEqual(
            result["top_values"], [{"value": "a", "count": 9}, {"value": "b", "count": 1}]
        )
        q = self.agg.queries[-1]
        self.assertEqual(q.groups, (ValuesGrouping("pod", Limits().max_top_values),))
        self.assertTrue(q.metrics[0].descending)


class CompareWindowsTest(AnalyticsTestCase):
    def test_default_clock_is_used(self):
        service = AnalyticsService(self.logs, self.agg)

        result = service.compare_windows(text="*", stream_id="s1", window_seconds=60)

        self.assertEqual(result["totals"]["current"], 0)

    def test_windows_totals_and_changes(self):
        totals = {"2026-09-25T11:00:00.000Z": 150, "2026-09-24T11:00:00.000Z": 100}
        self.logs.responder = lambda q: SearchPage(totals[q.window.start], (), 0)
        self.agg.tables = [
            table((["timeout"], {"count": 90}), (["refused"], {"count": 10})),  # current
            table((["timeout"], {"count": 40}), (["disk full"], {"count": 60})),  # baseline
        ]
        result = self.service.compare_windows(
            text="level:3", stream_id="s1", window_seconds=3600, field="error"
        )

        self.assertEqual(
            result["current_window"],
            {"from": "2026-09-25T11:00:00.000Z", "to": "2026-09-25T12:00:00.000Z"},
        )
        self.assertEqual(
            result["baseline_window"],
            {"from": "2026-09-24T11:00:00.000Z", "to": "2026-09-24T12:00:00.000Z"},
        )
        self.assertEqual(
            result["totals"],
            {"current": 150, "baseline": 100, "change": 50, "ratio": 1.5, "status": "up"},
        )
        changes = {c["value"]: c for c in result["biggest_changes"]}
        self.assertEqual(changes["timeout"]["change"], 50)
        self.assertEqual(changes["disk full"]["status"], "gone")
        self.assertEqual(changes["refused"]["status"], "new")
        self.assertEqual(
            result["biggest_changes"][0]["value"], "disk full"
        )  # |-60| is the largest change

    def test_totals_only_without_field(self):
        self.logs.total_hits = 5
        result = self.service.compare_windows(text="*", stream_id="s1", window_seconds=600)
        self.assertNotIn("biggest_changes", result)
        self.assertEqual(self.agg.queries, [])

    def test_overlapping_windows_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self.service.compare_windows(
                text="*", stream_id="s1", window_seconds=7200, baseline_offset_seconds=3600
            )


class FieldStatsTest(AnalyticsTestCase):
    def test_ungrouped_stats(self):
        self.agg.tables = [
            table(
                (
                    [],
                    {
                        "count(took_ms)": 10,
                        "avg(took_ms)": 12.5,
                        "min(took_ms)": 1,
                        "max(took_ms)": 99,
                        "sum(took_ms)": 125,
                        "p50(took_ms)": 10,
                        "p95(took_ms)": 80,
                    },
                )
            )
        ]
        result = self.service.field_stats(
            text="*", stream_id="s1", field="took_ms", seconds=60, percentiles="50,95"
        )
        self.assertEqual(self.agg.queries[-1].groups, ())
        self.assertEqual(
            result["stats"],
            {"count": 10, "avg": 12.5, "min": 1, "max": 99, "sum": 125, "p50": 10, "p95": 80},
        )

    def test_grouped_and_empty(self):
        self.agg.tables = [table((["api"], {"count(t)": 2, "avg(t)": 3.0}))]
        result = self.service.field_stats(
            text="*", stream_id="s1", field="t", seconds=60, percentiles="", group_by="source"
        )
        self.assertEqual(result["groups"][0]["value"], "api")
        self.assertEqual(result["groups"][0]["stats"]["avg"], 3.0)
        self.assertIsNone(result["groups"][0]["stats"]["max"])

        self.agg.tables = []
        empty = self.service.field_stats(
            text="*", stream_id="s1", field="t", seconds=60, percentiles=""
        )
        self.assertEqual(empty["stats"]["count"], 0)

    def test_bad_percentiles(self):
        with self.assertRaises(InvalidRequestError):
            self.service.field_stats(
                text="*", stream_id="s1", field="t", seconds=60, percentiles="high"
            )


class MessageContextTest(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.logs.stored[("idx", "m0")] = {
            "_id": "m0",
            "timestamp": "2026-09-25T10:00:00.000Z",
            "source": 'api "eu"',
            "msg": "anchor",
        }

        def respond(query):
            if query.sort.direction is SortDirection.DESC:  # earlier messages, newest first
                ids = ["m0", "b1", "b1", "b2", "b3"]
            else:  # later messages, oldest first
                ids = ["m0", "a1", "b1", "a2"]
            entries = tuple(LogEntry({"_id": i, "msg": i}) for i in ids)
            return SearchPage(len(ids), entries, len(ids))

        self.logs.responder = respond

    def test_neighbours_are_ordered_deduplicated_and_scoped(self):
        result = self.service.message_context(
            index="idx", message_id="m0", stream_id="s1", before=2, after=2
        )
        self.assertEqual([m["msg"] for m in result["before"]], ["b2", "b1"])  # chronological
        self.assertEqual([m["msg"] for m in result["after"]], ["a1", "a2"])  # b1 already shown
        self.assertEqual(result["anchor"]["msg"], "anchor")
        self.assertEqual(result["context_value"], 'api "eu"')

        before_q, after_q = self.logs.queries
        self.assertEqual(before_q.text, 'source:"api \\"eu\\""')
        self.assertEqual(
            before_q.window, Between("2026-09-25T09:55:00.000Z", "2026-09-25T10:00:00.000Z")
        )
        self.assertEqual(
            after_q.window, Between("2026-09-25T10:00:00.000Z", "2026-09-25T10:05:00.000Z")
        )

    def test_missing_context_field_falls_back_to_stream(self):
        result = self.service.message_context(
            index="idx", message_id="m0", stream_id="s1", before=1, after=0, context_field="pod"
        )
        self.assertEqual(self.logs.queries[0].text, "*")
        self.assertIn("'pod'", result["note"])
        self.assertEqual(len(self.logs.queries), 1)  # after=0 skips the second search

    def test_list_value_requires_every_item(self):
        self.logs.stored[("idx", "m0")]["tags"] = ["web", 'a"b']
        result = self.service.message_context(
            index="idx", message_id="m0", stream_id="s1", context_field="tags"
        )
        self.assertEqual(self.logs.queries[0].text, 'tags:"web" AND tags:"a\\"b"')
        self.assertEqual(result["context_value"], ["web", 'a"b'])
        self.assertIsNone(result["note"])

    def test_boolean_value_uses_lowercase_literal(self):
        self.logs.stored[("idx", "m0")]["retry"] = True
        self.service.message_context(
            index="idx", message_id="m0", stream_id="s1", context_field="retry"
        )
        self.assertEqual(self.logs.queries[0].text, 'retry:"true"')

    def test_unmatchable_value_falls_back_to_stream_with_reason(self):
        for value, kind in (([], "list"), ({"k": "v"}, "dict"), (["a", None], "list")):
            with self.subTest(value=value):
                self.logs.stored[("idx", "m0")]["meta"] = value
                self.logs.queries.clear()
                result = self.service.message_context(
                    index="idx", message_id="m0", stream_id="s1", context_field="meta"
                )
                self.assertEqual(self.logs.queries[0].text, "*")
                self.assertIsNone(result["context_field"])
                self.assertIsNone(result["context_value"])
                self.assertIn(f"'meta' value ({kind}) cannot be matched", result["note"])

    def test_validation(self):
        with self.assertRaises(InvalidRequestError):
            self.service.message_context(index="", message_id="m0", stream_id="s1")
        with self.assertRaises(InvalidRequestError):
            self.service.message_context(index="idx", message_id="m0", stream_id="")
        with self.assertRaises(InvalidRequestError):
            self.service.message_context(
                index="idx", message_id="m0", stream_id="s1", context_field="a b"
            )

    def test_message_without_timestamp(self):
        self.logs.stored[("idx", "m9")] = {"_id": "m9"}
        with self.assertRaises(BackendError):
            self.service.message_context(index="idx", message_id="m9", stream_id="s1")

    def test_unparseable_stored_timestamp_is_a_backend_error(self):
        self.logs.stored[("idx", "m9")] = {"_id": "m9", "timestamp": "yesterday"}
        with self.assertRaisesRegex(BackendError, "'yesterday' is not a recognised date"):
            self.service.message_context(index="idx", message_id="m9", stream_id="s1")


if __name__ == "__main__":
    unittest.main()
