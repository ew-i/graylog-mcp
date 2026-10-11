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
    is_internal_field,
)


class SortDirectionTest(unittest.TestCase):
    def test_given_padded_upper_case_when_parsing_then_direction_is_found(self):
        # Given
        raw = " ASC "

        # When
        direction = SortDirection.parse(raw)

        # Then
        self.assertIs(direction, SortDirection.ASC)

    def test_given_unknown_direction_when_parsing_then_rejects_it(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            SortDirection.parse("sideways")

    def test_given_field_and_direction_when_building_param_then_returns_field_colon_direction(self):
        # Given
        sort = Sort("level", SortDirection.ASC)

        # When
        param = sort.as_param()

        # Then
        self.assertEqual(param, "level:asc")

    def test_given_blank_field_when_creating_sort_then_rejects_the_sort(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            Sort(" ")


class TimeWindowTest(unittest.TestCase):
    def test_given_zero_seconds_when_creating_window_then_rejects_the_window(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            LastSeconds(0)

    def test_given_blank_end_when_creating_between_then_rejects_the_window(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            Between("2026-01-01T00:00:00Z", " ")

    def test_given_windows_when_describing_then_returns_relative_or_absolute_shape(self):
        # Given
        relative, absolute = LastSeconds(60), Between("a", "b")

        # When
        described = (relative.describe(), absolute.describe())

        # Then
        self.assertEqual(described[0], {"relative_seconds": 60})
        self.assertEqual(described[1], {"from": "a", "to": "b"})


class LogQueryTest(unittest.TestCase):
    def _make(self, **overrides):
        args = {"text": "*", "stream_id": "s1", "window": LastSeconds(60), "max_results": 10}
        args.update(overrides)
        return LogQuery(**args)

    def test_given_no_sort_when_creating_query_then_sorts_newest_first(self):
        # When
        query = self._make()

        # Then
        self.assertEqual(query.sort.as_param(), "timestamp:desc")

    def test_given_blank_text_when_creating_query_then_rejects_the_query(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self._make(text="  ")

    def test_given_no_stream_when_creating_query_then_rejects_it_with_a_browse_streams_hint(self):
        # When
        with self.assertRaises(InvalidRequestError) as raised:
            self._make(stream_id="")

        # Then
        self.assertIn("browse_streams", str(raised.exception))

    def test_given_zero_results_when_creating_query_then_rejects_the_query(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self._make(max_results=0)

    def test_given_negative_offset_when_creating_query_then_rejects_the_query(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self._make(offset=-1)


class ListingQueryTest(unittest.TestCase):
    def test_given_only_a_limit_when_creating_listing_query_then_defaults_and_bounds_hold(self):
        # When
        query = ListingQuery(5)

        # Then
        self.assertEqual(query, ListingQuery(limit=5, text="", offset=0))
        with self.assertRaises(InvalidRequestError):
            ListingQuery(0)
        with self.assertRaises(InvalidRequestError):
            ListingQuery(5, offset=-1)


class InternalFieldTest(unittest.TestCase):
    def test_given_field_names_when_classifying_then_only_graylog_bookkeeping_is_internal(self):
        for name in ("_id", "streams", "gl2_source_node"):
            with self.subTest(name=name):
                # When / Then
                self.assertTrue(is_internal_field(name))
        for name in ("message", "source", "id", "stream"):
            with self.subTest(name=name):
                # When / Then
                self.assertFalse(is_internal_field(name))


class LogEntryTest(unittest.TestCase):
    entry = LogEntry({"_id": "x", "streams": ["s"], "gl2_source_node": "n", "msg": "boom", "level": 3})

    def test_given_internal_fields_when_listing_visible_then_they_are_hidden(self):
        # When
        visible = self.entry.visible()

        # Then
        self.assertEqual(visible, {"msg": "boom", "level": 3})

    def test_given_explicit_selection_when_listing_visible_then_includes_internal_fields(self):
        # Given
        selection = ("_id", "msg", "absent")

        # When
        visible = self.entry.visible(selection)

        # Then
        self.assertEqual(visible, {"_id": "x", "msg": "boom"})

    def test_given_index_and_id_when_listing_entry_then_both_are_added(self):
        # Given
        entry = LogEntry({"_id": "x", "msg": "boom"}, index="graylog_0")

        # When
        listed = entry.listed(("msg",))

        # Then
        self.assertEqual(listed, {"msg": "boom", "_index": "graylog_0", "_id": "x"})
        self.assertEqual(LogEntry({"msg": "boom"}).listed(), {"msg": "boom"})


class AnalysisTest(unittest.TestCase):
    def test_given_repeated_values_when_ranking_then_returns_most_frequent_first_up_to_top(self):
        # Given
        entries = [LogEntry({"pod": p}) for p in ["a", "b", "a", "c", "a", "b"]]

        # When
        ranked = rank_values(entries, "pod", top=2)

        # Then
        self.assertEqual([(v.value, v.count) for v in ranked], [("a", 3), ("b", 2)])

    def test_given_missing_null_and_mixed_types_when_ranking_then_skips_missing_and_merges_types(self):
        # Given
        entries = [
            LogEntry({"code": 500}),
            LogEntry({"code": "500"}),
            LogEntry({"code": None}),
            LogEntry({}),
        ]

        # When
        ranked = rank_values(entries, "code", top=10)

        # Then
        self.assertEqual([(v.value, v.count) for v in ranked], [("500", 2)])

    def test_given_entries_when_collecting_field_names_then_returns_sorted_union_without_internal_fields(self):
        # Given
        entries = [
            LogEntry({"b": 1, "a": 1, "_id": "x", "gl2_source_node": "n"}),
            LogEntry({"c": 1, "a": 2, "streams": ["s1"]}),
        ]

        # When
        names = field_names(entries)

        # Then
        self.assertEqual(names, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
