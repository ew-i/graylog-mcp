import unittest
from datetime import datetime, timezone

from graylog_mcp.application.policy import parse_percentiles
from graylog_mcp.domain.aggregation import (
    AggregationQuery,
    Metric,
    TimeGrouping,
    ValuesGrouping,
    auto_interval,
    interval_seconds,
    require_field_name,
)
from graylog_mcp.domain.alerts import EventQuery, priority_label
from graylog_mcp.domain.configuration import StreamRule
from graylog_mcp.domain.errors import InvalidRequestError
from graylog_mcp.domain.models import LastSeconds
from graylog_mcp.domain.operations import InputNodeState, InputStatus, NodeStatus
from graylog_mcp.domain.timeutil import format_timestamp, parse_timestamp


class IntervalTest(unittest.TestCase):
    def test_interval_seconds(self):
        self.assertEqual(interval_seconds("5m"), 300)
        self.assertEqual(interval_seconds("2d"), 172800)

    def test_bad_intervals(self):
        for bad in ("", "0m", "5x", "m5", "1.5h"):
            with self.subTest(bad=bad), self.assertRaises(InvalidRequestError):
                interval_seconds(bad)

    def test_auto_interval_respects_bucket_budget(self):
        self.assertEqual(auto_interval(3600, 60), "1m")
        self.assertEqual(auto_interval(86400, 48), "30m")
        self.assertEqual(auto_interval(30 * 86400, 48), "1d")
        self.assertEqual(auto_interval(10**12, 1), "1y")

    def test_time_grouping_validates(self):
        with self.assertRaises(InvalidRequestError):
            TimeGrouping("soon")


class MetricTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(Metric("count").label, "count")
        self.assertEqual(Metric("avg", "took_ms").label, "avg(took_ms)")
        self.assertEqual(Metric("percentile", "took_ms", percentile=90).label, "p90(took_ms)")
        self.assertEqual(Metric("percentile", "t", percentile=99.9).label, "p99.9(t)")

    def test_validation(self):
        with self.assertRaises(InvalidRequestError):
            Metric("median", "x")
        with self.assertRaises(InvalidRequestError):
            Metric("avg")  # needs a field
        with self.assertRaises(InvalidRequestError):
            Metric("percentile", "x")  # needs a percentile
        with self.assertRaises(InvalidRequestError):
            Metric("percentile", "x", percentile=0)
        with self.assertRaises(InvalidRequestError):
            Metric("avg", "x", percentile=50)
        with self.assertRaises(InvalidRequestError):
            ValuesGrouping("x", 0)

    def test_percentile_parser_rejects_invalid_values(self):
        self.assertEqual(parse_percentiles("50, 50, 99.9"), (50.0, 99.9))
        for raw in ("nope", "0", "101"):
            with self.subTest(raw=raw), self.assertRaises(InvalidRequestError):
                parse_percentiles(raw)

    def test_field_names_are_checked(self):
        self.assertEqual(require_field_name(" kubernetes.pod_name "), "kubernetes.pod_name")
        with self.assertRaises(InvalidRequestError):
            require_field_name('msg:"x" OR *')
        with self.assertRaises(InvalidRequestError):
            ValuesGrouping("", 5)


class AggregationQueryTest(unittest.TestCase):
    def test_requires_scope_and_distinct_metrics(self):
        window = LastSeconds(60)
        with self.assertRaises(InvalidRequestError):
            AggregationQuery("*", "", window, (), (Metric("count"),))
        with self.assertRaises(InvalidRequestError):
            AggregationQuery("*", "s1", window, (), ())
        with self.assertRaises(InvalidRequestError):
            AggregationQuery("*", "s1", window, (), (Metric("count"), Metric("count")))


class TimeUtilTest(unittest.TestCase):
    def test_parse_variants(self):
        expected = datetime(2026, 3, 15, 10, 0, 0, 123000, tzinfo=timezone.utc)
        self.assertEqual(parse_timestamp("2026-03-15T10:00:00.123Z"), expected)
        self.assertEqual(parse_timestamp("2026-03-15 10:00:00.123"), expected)
        self.assertEqual(parse_timestamp("2026-03-15T12:00:00.123+02:00"), expected)

    def test_parse_rejects_garbage(self):
        with self.assertRaises(InvalidRequestError):
            parse_timestamp("yesterday")

    def test_format_round_trip(self):
        self.assertEqual(
            format_timestamp(parse_timestamp("2026-03-15T10:00:00.123Z")),
            "2026-03-15T10:00:00.123Z",
        )


class AlertModelTest(unittest.TestCase):
    def test_priority_labels(self):
        self.assertEqual(
            [priority_label(p) for p in (1, 2, 3, 9, None)], ["low", "normal", "high", "9", None]
        )

    def test_event_query_limit(self):
        with self.assertRaises(InvalidRequestError):
            EventQuery(window=LastSeconds(60), limit=0)

    def test_event_query_offset(self):
        with self.assertRaises(InvalidRequestError):
            EventQuery(window=LastSeconds(60), limit=1, offset=-1)


class StreamRuleTest(unittest.TestCase):
    def rule(self, code, value="3", inverted=False, field="level"):
        return StreamRule(
            field=field, type_code=code, value=value, inverted=inverted, description=None
        )

    def test_meanings(self):
        self.assertEqual(self.rule(1).meaning(), "level must match exactly '3'")
        self.assertEqual(
            self.rule(6, "err", inverted=True).meaning(), "level must not contain 'err'"
        )
        self.assertEqual(self.rule(5).meaning(), "level must be present")
        self.assertEqual(self.rule(7).meaning(), "every message matches")
        self.assertEqual(self.rule(8, "in1").meaning(), "input must be 'in1'")
        self.assertIn("unknown rule type 42", self.rule(42).meaning())
        self.assertEqual(self.rule(2).type_name, "regex")


class HealthTest(unittest.TestCase):
    def test_input_health(self):
        running = InputNodeState("n1", "RUNNING", None, None)
        failed = InputNodeState("n2", "FAILED", None, "port in use")
        self.assertEqual(InputStatus("i", "t", "gelf", True, None, ()).health, "unknown")
        self.assertEqual(InputStatus("i", "t", "gelf", True, None, (running,)).health, "ok")
        self.assertEqual(
            InputStatus("i", "t", "gelf", True, None, (running, failed)).health, "problem"
        )

    def test_node_health(self):
        self.assertTrue(NodeStatus("n", True, is_processing=True, lifecycle="running").healthy)
        self.assertFalse(NodeStatus("n", True, is_processing=False).healthy)
        self.assertFalse(NodeStatus("n", True, lifecycle="throttled").healthy)
        self.assertFalse(NodeStatus("n", False).healthy)


if __name__ == "__main__":
    unittest.main()
