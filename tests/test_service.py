import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

from graylog_mcp.application.pagination import open_page
from graylog_mcp.application.service import Limits, LogService, parse_field_list
from graylog_mcp.domain.errors import InvalidRequestError, NotFoundError
from graylog_mcp.domain.models import (
    Between,
    FieldInfo,
    LastSeconds,
    LogEntry,
    QueryProblem,
    QueryValidation,
    SearchPage,
    SortDirection,
    Stream,
)

from .fakes import FakeLogStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _cursor(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


class ParseFieldListTest(unittest.TestCase):
    def test_given_messy_list_when_parsing_fields_then_trims_dedupes_and_drops_blanks(self):
        # When / Then
        self.assertEqual(parse_field_list(" a, b,,a ,c "), ("a", "b", "c"))

    def test_given_empty_text_when_parsing_fields_then_returns_no_fields(self):
        # When / Then
        self.assertEqual(parse_field_list(""), ())

    def test_given_malformed_cursors_when_opening_page_then_rejects_each_cursor(self):
        # Given
        for cursor in (
            "%",
            _cursor(["not", "a", "dict"]),
            _cursor({"request": {"kind": "search", "limit": 1}, "offset": "one"}),
            _cursor({"request": {"kind": "search", "limit": 0}, "offset": 0}),
            _cursor({"request": "search", "offset": 0}),
        ):
            # When / Then
            with self.subTest(cursor=cursor), self.assertRaises(InvalidRequestError):
                open_page("search", cursor, limit=1, max_limit=10)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.store = FakeLogStore()
        self.now = NOW
        self.service = LogService(self.store, clock=lambda: self.now)


class CatalogueTest(ServiceTestCase):
    def test_given_cluster_when_reading_info_then_returns_a_plain_dict(self):
        # When
        info = self.service.cluster_info()

        # Then
        self.assertEqual(info["version"], "6.1.0")

    def test_given_stream_when_browsing_then_returns_id_title_description_and_disabled(self):
        # Given
        self.store.stream_list = [Stream("s1", "api", "API pods", False)]

        # When
        result = self.service.streams()

        # Then
        self.assertEqual(result["total"], 1)
        self.assertEqual(
            result["streams"],
            [{"id": "s1", "title": "api", "description": "API pods", "disabled": False}],
        )

    def test_given_two_streams_when_following_cursor_then_returns_second_page_and_ends(self):
        # Given
        self.store.stream_list = [
            Stream("s1", "one", None, False),
            Stream("s2", "two", None, False),
        ]

        # When
        first = self.service.streams(limit=1)
        second = self.service.streams(limit=1, next_cursor=first["next_cursor"])

        # Then
        self.assertEqual([s["id"] for s in first["streams"] + second["streams"]], ["s1", "s2"])
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))


class SearchTest(ServiceTestCase):
    def test_given_recent_search_when_searching_then_builds_query_and_shapes_result(self):
        # Given
        self.store.entries = [{"msg": "hi", "gl2_x": 1, "level": 3}]

        # When
        result = self.service.search_recent(text="level:3", stream_id="s1", seconds=120, limit=5, sort_order="ASC")

        # Then
        q = self.store.last_query
        self.assertEqual((q.text, q.stream_id, q.max_results), ("level:3", "s1", 5))
        self.assertEqual(q.window, Between("2026-10-07T11:58:00.000Z", "2026-10-07T12:00:00.000Z"))
        self.assertIs(q.sort.direction, SortDirection.ASC)
        self.assertEqual(result["messages"], [{"msg": "hi", "level": 3}])
        self.assertEqual(
            result["window"],
            {
                "from": "2026-10-07T11:58:00.000Z",
                "to": "2026-10-07T12:00:00.000Z",
                "relative_seconds": 120,
            },
        )
        self.assertEqual(result["stream_id"], "s1")
        self.assertEqual((result["total_results"], result["returned"]), (1, 1))

    def test_given_out_of_range_limit_and_window_when_searching_then_clamps_them(self):
        # Given
        service = LogService(self.store, Limits(max_results=10, max_window_seconds=100), clock=lambda: NOW)

        # When
        service.search_recent(text="*", stream_id="s1", seconds=10**9, limit=10**9)

        # Then
        self.assertEqual(self.store.last_query.max_results, 10)
        self.assertEqual(
            self.store.last_query.window,
            Between("2026-10-07T11:58:20.000Z", "2026-10-07T12:00:00.000Z"),
        )

        # When
        service.search_recent(text="*", stream_id="s1", seconds=-5, limit=-5)

        # Then
        self.assertEqual(self.store.last_query.max_results, 1)
        self.assertEqual(
            self.store.last_query.window,
            Between("2026-10-07T11:59:59.000Z", "2026-10-07T12:00:00.000Z"),
        )

    def test_given_field_selection_when_searching_then_returns_only_those_fields(self):
        # Given
        self.store.entries = [{"msg": "hi", "level": 3, "pod": "p"}]

        # When
        result = self.service.search_recent(text="*", stream_id="s1", seconds=60, limit=5, fields="pod,msg")

        # Then
        self.assertEqual(result["messages"], [{"pod": "p", "msg": "hi"}])
        self.assertEqual(self.store.last_query.only_fields, ("pod", "msg"))

    def test_given_indexed_message_when_searching_then_it_carries_index_and_id(self):
        # Given
        self.store.responder = lambda _query: SearchPage(1, (LogEntry({"_id": "m1", "msg": "hi"}, index="idx_1"),), 1)

        # When
        result = self.service.search_recent(text="*", stream_id="s1", seconds=60, limit=5, fields="msg")

        # Then
        self.assertEqual(result["messages"], [{"msg": "hi", "_index": "idx_1", "_id": "m1"}])

    def test_given_absolute_range_when_searching_then_passes_the_window_through(self):
        # When
        result = self.service.search_between(text="*", stream_id="s1", start="a", end="b", limit=5)

        # Then
        self.assertEqual(self.store.last_query.window, Between("a", "b"))
        self.assertEqual(result["window"], {"from": "a", "to": "b"})

    def test_given_three_matches_when_following_cursor_then_continues_at_offset(self):
        # Given
        self.store.entries = [{"msg": str(i)} for i in range(3)]

        # When
        first = self.service.search_recent(text="*", stream_id="s1", seconds=60, limit=2)
        second = self.service.search_recent(
            text="*",
            stream_id="s1",
            seconds=60,
            limit=2,
            next_cursor=first["next_cursor"],
        )

        # Then
        self.assertEqual([m["msg"] for m in first["messages"] + second["messages"]], ["0", "1", "2"])
        self.assertEqual(self.store.last_query.offset, 2)
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual((first["truncated"], second["truncated"]), (False, False))

    def test_given_invalid_input_when_searching_then_rejects_it_before_the_store(self):
        # Given
        for kwargs in (
            {"text": " ", "stream_id": "s1"},
            {"text": "*", "stream_id": ""},
            {"text": "*", "stream_id": "s1", "sort_order": "up"},
        ):
            # When / Then
            with self.subTest(**kwargs), self.assertRaises(InvalidRequestError):
                self.service.search_recent(seconds=60, limit=5, **kwargs)

        # Then
        self.assertEqual(self.store.queries, [])


class SearchPaginationTest(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.store.entries = [{"msg": str(i), "pod": "p"} for i in range(5)]

    def _first(self, **overrides):
        args = {"text": "level:3", "stream_id": "s1", "seconds": 3600, "limit": 2}
        return self.service.search_recent(**{**args, **overrides})

    def test_given_time_passes_when_following_cursor_then_keeps_the_first_window(self):
        # Given
        first = self._first()
        first_window = self.store.last_query.window
        self.now = NOW + timedelta(minutes=10)

        # When
        second = self.service.search_recent(
            text="level:3",
            stream_id="s1",
            seconds=3600,
            limit=2,
            next_cursor=first["next_cursor"],
        )

        # Then
        self.assertEqual(self.store.last_query.window, first_window)
        self.assertEqual(second["window"], first["window"])

    def test_given_other_arguments_with_cursor_when_searching_then_uses_the_cursor_request(self):
        # Given
        first = self._first(fields="msg", sort_order="asc")

        # When
        self.service.search_recent(
            text="level:3",
            stream_id="",
            seconds=60,
            limit=500,
            next_cursor=first["next_cursor"],
        )

        # Then
        q = self.store.last_query
        self.assertEqual((q.stream_id, q.max_results, q.offset), ("s1", 2, 2))
        self.assertEqual((q.only_fields, q.sort.direction), (("msg",), SortDirection.ASC))

    def test_given_cursor_for_another_stream_when_searching_then_rejects_the_cursor(self):
        # Given
        first = self._first()

        # When
        with self.assertRaises(InvalidRequestError) as raised:
            self.service.search_recent(
                text="level:3",
                stream_id="s2",
                seconds=3600,
                limit=2,
                next_cursor=first["next_cursor"],
            )

        # Then
        self.assertRegex(str(raised.exception), "stream_id")

    def test_given_cursor_from_another_tool_when_searching_then_rejects_the_cursor(self):
        # Given
        self.store.stream_list = [Stream(f"s{i}", "t", None, False) for i in range(3)]
        streams = self.service.streams(limit=1)

        # When
        with self.assertRaises(InvalidRequestError) as raised:
            self.service.search_recent(text="*", stream_id="s1", seconds=60, limit=1, next_cursor=streams["next_cursor"])

        # Then
        self.assertRegex(str(raised.exception), "different tool")

    def test_given_limit_above_maximum_when_searching_then_reports_truncation_once(self):
        # Given
        service = LogService(self.store, Limits(max_results=2), clock=lambda: NOW)

        # When
        first = service.search_recent(text="*", stream_id="s1", seconds=60, limit=10)

        # Then
        self.assertEqual((first["limit"], first["has_more"], first["truncated"]), (2, True, True))
        self.assertIn("limit 10", first["truncation"])

        # When
        second = service.search_recent(text="*", stream_id="s1", seconds=60, limit=10, next_cursor=first["next_cursor"])

        # Then
        self.assertFalse(second["truncated"])
        self.assertNotIn("truncation", second)

    def test_given_everything_fits_when_searching_then_does_not_report_truncation(self):
        # Given
        service = LogService(self.store, Limits(max_results=2), clock=lambda: NOW)
        self.store.entries = self.store.entries[:2]

        # When
        result = service.search_recent(text="*", stream_id="s1", seconds=60, limit=10)

        # Then
        self.assertEqual((result["has_more"], result["truncated"]), (False, False))

    def test_given_result_window_limit_when_paging_then_stops_and_says_why(self):
        # Given
        service = LogService(self.store, Limits(max_result_window=4), clock=lambda: NOW)

        # When
        first = service.search_recent(text="*", stream_id="s1", seconds=60, limit=2)
        second = service.search_recent(text="*", stream_id="s1", seconds=60, limit=2, next_cursor=first["next_cursor"])

        # Then
        self.assertEqual((first["has_more"], first["truncated"]), (True, False))
        self.assertEqual((second["has_more"], second["next_cursor"]), (False, None))
        self.assertTrue(second["truncated"])
        self.assertIn("first 4 of 5", second["truncation"])

    def test_given_cursor_edited_to_huge_limit_when_searching_then_clamps_the_limit(self):
        # Given
        first = self._first()
        payload = json.loads(base64.urlsafe_b64decode(first["next_cursor"] + "=="))
        payload["request"]["limit"] = 10**6

        # When
        self._first(next_cursor=_cursor(payload))

        # Then
        self.assertEqual(self.store.last_query.max_results, Limits().max_results)

    def test_given_cursor_with_edited_values_when_searching_then_rejects_the_cursor(self):
        # Given
        payload = json.loads(base64.urlsafe_b64decode(self._first()["next_cursor"] + "=="))
        for name, bad in (("sort_field", 3), ("window", {"from": "x"})):
            edited = json.loads(json.dumps(payload))
            edited["request"][name] = bad
            with self.subTest(name=name):
                # When
                with self.assertRaises(InvalidRequestError) as raised:
                    self._first(next_cursor=_cursor(edited))

                # Then
                self.assertRegex(str(raised.exception), "invalid")

    def test_given_empty_page_despite_total_when_searching_then_paging_ends(self):
        # Given
        self.store.entries = []
        self.store.total_hits = 5

        # When
        result = self._first()

        # Then
        self.assertEqual((result["has_more"], result["next_cursor"]), (False, None))


class MessageTest(ServiceTestCase):
    def test_given_stored_message_when_reading_then_hides_internal_fields(self):
        # Given
        self.store.stored[("idx_1", "m1")] = {"_id": "m1", "msg": "x"}

        # When
        message = self.service.message(index=" idx_1 ", message_id="m1")

        # Then
        self.assertEqual(message, {"msg": "x"})

    def test_given_no_index_when_reading_message_then_rejects_the_request(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self.service.message(index="", message_id="m1")

    def test_given_unknown_message_when_reading_then_raises_not_found(self):
        # When / Then
        with self.assertRaises(NotFoundError):
            self.service.message(index="idx", message_id="nope")


class TopValuesTest(ServiceTestCase):
    def test_given_sample_when_ranking_values_then_ranks_them_from_minimal_fields(self):
        # Given
        self.store.entries = [{"pod": "a"}, {"pod": "b"}, {"pod": "a"}]

        # When
        result = self.service.top_values(text="*", stream_id="s1", field="pod", seconds=60, top=5)

        # Then
        self.assertEqual(result["top_values"], [{"value": "a", "count": 2}, {"value": "b", "count": 1}])
        self.assertEqual(self.store.last_query.only_fields, ("timestamp", "pod"))
        self.assertEqual(self.store.last_query.max_results, Limits().aggregation_sample)
        self.assertNotIn("note", result)

    def test_given_more_matches_than_sampled_when_ranking_then_note_says_so(self):
        # Given
        self.store.entries = [{"pod": "a"}]
        self.store.total_hits = 5000

        # When
        result = self.service.top_values(text="*", stream_id="s1", field="pod", seconds=60, top=5)

        # Then
        self.assertIn("5000", result["note"])

    def test_given_blank_field_when_ranking_values_then_rejects_the_field(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self.service.top_values(text="*", stream_id="s1", field=" ", seconds=60, top=5)


class DiscoverFieldsTest(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.store.stream_list = [Stream("s1", "api", None, False)]

    def test_given_field_types_when_discovering_then_returns_typed_fields_without_internal_ones(self):
        # Given
        self.store.field_info = [
            FieldInfo("message", "string", False, False),
            FieldInfo("gl2_source_node", "string", True, False),
            FieldInfo("duration_ms", "long", True, True),
            FieldInfo("streams", "string", True, False),
        ]
        self.store.entries = [{"message": "x", "duration_ms": 1, "gl2_remote_ip": "10.0.0.1"}]

        # When
        result = self.service.discover_fields(stream_id=" s1 ", seconds=60)

        # Then
        self.assertEqual((result["source"], result["sampled_messages"]), ("field types", 1))
        self.assertEqual(
            result["fields"],
            [
                {"name": "duration_ms", "type": "long", "groupable": True, "numeric": True},
                {"name": "message", "type": "string", "groupable": False, "numeric": False},
            ],
        )
        self.assertNotIn("note", result)
        self.assertEqual(self.store.field_type_requests, [("s1", LastSeconds(60))])

    def test_given_new_untyped_field_when_discovering_then_adds_it_from_the_sample_with_a_note(self):
        # Given
        self.store.field_info = [FieldInfo("message", "string", False, False)]
        self.store.entries = [{"message": "x", "error_type": "E"}]

        # When
        result = self.service.discover_fields(stream_id="s1", seconds=60)

        # Then
        self.assertEqual(
            result["fields"][0],
            {"name": "error_type", "type": None, "groupable": None, "numeric": None},
        )
        self.assertIn("error_type appear in recent messages but have no type yet", result["note"])

    def test_given_no_field_types_api_when_discovering_then_returns_names_from_the_sample(self):
        # Given
        self.store.entries = [{"a": 1}, {"b": 2, "a": 3, "gl2_remote_ip": "10.0.0.1"}]

        # When
        result = self.service.discover_fields(stream_id=" s1 ", seconds=60)

        # Then
        self.assertEqual((result["source"], result["sampled_messages"]), ("message sample", 2))
        self.assertEqual(
            result["fields"],
            [
                {"name": "a", "type": None, "groupable": None, "numeric": None},
                {"name": "b", "type": None, "groupable": None, "numeric": None},
            ],
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["stream_id"], "s1")
        self.assertEqual((self.store.last_query.text, self.store.last_query.stream_id), ("*", "s1"))

    def test_given_unknown_stream_when_discovering_fields_then_raises_not_found(self):
        # When
        with self.assertRaises(NotFoundError) as raised:
            self.service.discover_fields(stream_id="nope", seconds=60)

        # Then
        self.assertRegex(str(raised.exception), "'nope' does not exist")
        self.assertEqual(self.store.queries, [])  # no search was sent

    def test_given_no_stream_when_discovering_fields_then_rejects_the_request(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self.service.discover_fields(stream_id="", seconds=60)


class ValidateQueryTest(ServiceTestCase):
    def test_given_unknown_field_warning_when_validating_then_reports_it_as_still_valid(self):
        # Given
        problem = QueryProblem("UNKNOWN_FIELD", "Query contains unknown field: nosuch", 1, 0)
        self.store.validation = QueryValidation("WARNING", (problem,))

        # When
        result = self.service.validate_query(text=" nosuch:x ", stream_id=" s1 ", seconds=60)

        # Then
        self.assertEqual((result["status"], result["valid"]), ("WARNING", True))
        self.assertEqual(
            result["problems"],
            [{"kind": "UNKNOWN_FIELD", "message": "Query contains unknown field: nosuch", "line": 1, "column": 0}],
        )
        self.assertEqual(self.store.validations, [("nosuch:x", "s1", LastSeconds(60))])
        self.assertEqual(self.store.queries, [])  # no search was sent

    def test_given_parse_error_when_validating_then_reports_it_invalid(self):
        # Given
        self.store.validation = QueryValidation("ERROR", (QueryProblem("QUERY_PARSING_ERROR", "x", 1, 5),))

        # When
        result = self.service.validate_query(text="(a", stream_id="s1", seconds=60)

        # Then
        self.assertFalse(result["valid"])

    def test_given_blank_query_when_validating_then_rejects_the_request(self):
        # When / Then
        with self.assertRaises(InvalidRequestError):
            self.service.validate_query(text=" ", stream_id="s1", seconds=60)


if __name__ == "__main__":
    unittest.main()
