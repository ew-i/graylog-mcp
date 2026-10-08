import unittest

from graylog_mcp.domain.analysis import field_names, rank_values
from graylog_mcp.domain.errors import InvalidRequestError
from graylog_mcp.domain.models import (
    Between,
    LastSeconds,
    ListingQuery,
    LogEntry,
    LogQuery,
    Sort,
    SortDirection,
)


class SortDirectionTest(unittest.TestCase):
    def test_parse_is_case_and_space_insensitive(self):
        self.assertIs(SortDirection.parse(" ASC "), SortDirection.ASC)

    def test_parse_rejects_unknown(self):
        with self.assertRaises(InvalidRequestError):
            SortDirection.parse("sideways")

    def test_sort_param(self):
        self.assertEqual(Sort("level", SortDirection.ASC).as_param(), "level:asc")

    def test_blank_field_rejected(self):
        with self.assertRaises(InvalidRequestError):
            Sort(" ")


class TimeWindowTest(unittest.TestCase):
    def test_last_seconds_must_be_positive(self):
        with self.assertRaises(InvalidRequestError):
            LastSeconds(0)

    def test_between_requires_both_ends(self):
        with self.assertRaises(InvalidRequestError):
            Between("2026-01-01T00:00:00Z", " ")

    def test_describe(self):
        self.assertEqual(LastSeconds(60).describe(), {"relative_seconds": 60})
        self.assertEqual(Between("a", "b").describe(), {"from": "a", "to": "b"})


class LogQueryTest(unittest.TestCase):
    def _make(self, **overrides):
        args = {"text": "*", "stream_id": "s1", "window": LastSeconds(60), "max_results": 10}
        args.update(overrides)
        return LogQuery(**args)

    def test_valid_query(self):
        self.assertEqual(self._make().sort.as_param(), "timestamp:desc")

    def test_blank_text_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self._make(text="  ")

    def test_missing_stream_rejected_with_hint(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            self._make(stream_id="")
        self.assertIn("browse_streams", str(ctx.exception))

    def test_non_positive_limit_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self._make(max_results=0)

    def test_negative_offset_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self._make(offset=-1)


class ListingQueryTest(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(ListingQuery(5), ListingQuery(limit=5, text="", offset=0))
        with self.assertRaises(InvalidRequestError):
            ListingQuery(0)
        with self.assertRaises(InvalidRequestError):
            ListingQuery(5, offset=-1)


class LogEntryTest(unittest.TestCase):
    entry = LogEntry(
        {"_id": "x", "streams": ["s"], "gl2_source_node": "n", "msg": "boom", "level": 3}
    )

    def test_hides_internal_fields_by_default(self):
        self.assertEqual(self.entry.visible(), {"msg": "boom", "level": 3})

    def test_explicit_selection_can_include_internal_fields(self):
        self.assertEqual(self.entry.visible(("_id", "msg", "absent")), {"_id": "x", "msg": "boom"})

    def test_listed_adds_index_and_id_when_known(self):
        entry = LogEntry({"_id": "x", "msg": "boom"}, index="graylog_0")
        self.assertEqual(
            entry.listed(("msg",)), {"msg": "boom", "_index": "graylog_0", "_id": "x"}
        )
        self.assertEqual(LogEntry({"msg": "boom"}).listed(), {"msg": "boom"})


class AnalysisTest(unittest.TestCase):
    def test_rank_values_orders_by_frequency_and_limits(self):
        entries = [LogEntry({"pod": p}) for p in ["a", "b", "a", "c", "a", "b"]]
        ranked = rank_values(entries, "pod", top=2)
        self.assertEqual([(v.value, v.count) for v in ranked], [("a", 3), ("b", 2)])

    def test_rank_values_skips_missing_and_null_and_merges_types(self):
        entries = [
            LogEntry({"code": 500}),
            LogEntry({"code": "500"}),
            LogEntry({"code": None}),
            LogEntry({}),
        ]
        ranked = rank_values(entries, "code", top=10)
        self.assertEqual([(v.value, v.count) for v in ranked], [("500", 2)])

    def test_field_names_is_sorted_union(self):
        entries = [LogEntry({"b": 1, "a": 1}), LogEntry({"c": 1, "a": 2})]
        self.assertEqual(field_names(entries), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
