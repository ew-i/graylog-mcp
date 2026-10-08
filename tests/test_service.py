import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

from graylog_mcp.application.pagination import open_page
from graylog_mcp.application.service import Limits, LogService, parse_field_list
from graylog_mcp.domain.errors import InvalidRequestError, NotFoundError
from graylog_mcp.domain.models import (
    Between,
    LogEntry,
    SearchPage,
    SortDirection,
    Stream,
)

from .fakes import FakeLogStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _cursor(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


class ParseFieldListTest(unittest.TestCase):
    def test_trims_drops_blanks_and_dedupes(self):
        self.assertEqual(parse_field_list(" a, b,,a ,c "), ("a", "b", "c"))

    def test_empty(self):
        self.assertEqual(parse_field_list(""), ())

    def test_invalid_cursor_payloads(self):
        for cursor in (
            "%",
            _cursor(["not", "a", "dict"]),
            _cursor({"request": {"kind": "search", "limit": 1}, "offset": "one"}),
            _cursor({"request": {"kind": "search", "limit": 0}, "offset": 0}),
            _cursor({"request": "search", "offset": 0}),
        ):
            with self.subTest(cursor=cursor), self.assertRaises(InvalidRequestError):
                open_page("search", cursor, limit=1, max_limit=10)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.store = FakeLogStore()
        self.now = NOW
        self.service = LogService(self.store, clock=lambda: self.now)


class CatalogueTest(ServiceTestCase):
    def test_cluster_info_is_plain_dict(self):
        self.assertEqual(self.service.cluster_info()["version"], "6.1.0")

    def test_streams_excludes_default_stream(self):
        self.store.stream_list = [
            Stream("d", "All messages", None, False, is_default=True),
            Stream("s1", "api", "API pods", False, is_default=False),
        ]
        result = self.service.streams()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["streams"][0]["id"], "s1")
        self.assertNotIn("is_default", result["streams"][0])

    def test_streams_cursor_returns_next_page(self):
        self.store.stream_list = [
            Stream("s1", "one", None, False, False),
            Stream("s2", "two", None, False, False),
        ]
        first = self.service.streams(limit=1)
        second = self.service.streams(limit=1, next_cursor=first["next_cursor"])
        self.assertEqual([s["id"] for s in first["streams"] + second["streams"]], ["s1", "s2"])
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))


class SearchTest(ServiceTestCase):
    def test_recent_search_builds_query_and_shapes_result(self):
        self.store.entries = [{"msg": "hi", "gl2_x": 1, "level": 3}]
        result = self.service.search_recent(
            text="level:3", stream_id="s1", seconds=120, limit=5, sort_order="ASC"
        )
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

    def test_limits_are_clamped(self):
        service = LogService(
            self.store, Limits(max_results=10, max_window_seconds=100), clock=lambda: NOW
        )
        service.search_recent(text="*", stream_id="s1", seconds=10**9, limit=10**9)
        self.assertEqual(self.store.last_query.max_results, 10)
        self.assertEqual(
            self.store.last_query.window,
            Between("2026-10-07T11:58:20.000Z", "2026-10-07T12:00:00.000Z"),
        )

        service.search_recent(text="*", stream_id="s1", seconds=-5, limit=-5)
        self.assertEqual(self.store.last_query.max_results, 1)
        self.assertEqual(
            self.store.last_query.window,
            Between("2026-10-07T11:59:59.000Z", "2026-10-07T12:00:00.000Z"),
        )

    def test_field_projection(self):
        self.store.entries = [{"msg": "hi", "level": 3, "pod": "p"}]
        result = self.service.search_recent(
            text="*", stream_id="s1", seconds=60, limit=5, fields="pod,msg"
        )
        self.assertEqual(result["messages"], [{"pod": "p", "msg": "hi"}])
        self.assertEqual(self.store.last_query.only_fields, ("pod", "msg"))

    def test_search_messages_carry_index_and_id(self):
        self.store.responder = lambda _query: SearchPage(
            1, (LogEntry({"_id": "m1", "msg": "hi"}, index="idx_1"),), 1
        )

        result = self.service.search_recent(
            text="*", stream_id="s1", seconds=60, limit=5, fields="msg"
        )

        self.assertEqual(result["messages"], [{"msg": "hi", "_index": "idx_1", "_id": "m1"}])

    def test_absolute_search(self):
        result = self.service.search_between(text="*", stream_id="s1", start="a", end="b", limit=5)
        self.assertEqual(self.store.last_query.window, Between("a", "b"))
        self.assertEqual(result["window"], {"from": "a", "to": "b"})

    def test_search_cursor_uses_offset(self):
        self.store.entries = [{"msg": str(i)} for i in range(3)]
        first = self.service.search_recent(text="*", stream_id="s1", seconds=60, limit=2)
        second = self.service.search_recent(
            text="*",
            stream_id="s1",
            seconds=60,
            limit=2,
            next_cursor=first["next_cursor"],
        )
        self.assertEqual(
            [m["msg"] for m in first["messages"] + second["messages"]], ["0", "1", "2"]
        )
        self.assertEqual(self.store.last_query.offset, 2)
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual((first["truncated"], second["truncated"]), (False, False))

    def test_invalid_input_never_reaches_store(self):
        for kwargs in (
            {"text": " ", "stream_id": "s1"},
            {"text": "*", "stream_id": ""},
            {"text": "*", "stream_id": "s1", "sort_order": "up"},
        ):
            with self.subTest(**kwargs), self.assertRaises(InvalidRequestError):
                self.service.search_recent(seconds=60, limit=5, **kwargs)
        self.assertEqual(self.store.queries, [])


class SearchPaginationTest(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.store.entries = [{"msg": str(i), "pod": "p"} for i in range(5)]

    def _first(self, **overrides):
        args = {"text": "level:3", "stream_id": "s1", "seconds": 3600, "limit": 2}
        return self.service.search_recent(**{**args, **overrides})

    def test_later_pages_keep_the_first_pages_window(self):
        first = self._first()
        first_window = self.store.last_query.window
        self.now = NOW + timedelta(minutes=10)

        second = self.service.search_recent(
            text="level:3",
            stream_id="s1",
            seconds=3600,
            limit=2,
            next_cursor=first["next_cursor"],
        )

        self.assertEqual(self.store.last_query.window, first_window)
        self.assertEqual(second["window"], first["window"])

    def test_cursor_carries_the_request(self):
        first = self._first(fields="msg", sort_order="asc")
        self.service.search_recent(
            text="level:3",
            stream_id="",
            seconds=60,
            limit=500,
            next_cursor=first["next_cursor"],
        )
        q = self.store.last_query
        self.assertEqual((q.stream_id, q.max_results, q.offset), ("s1", 2, 2))
        self.assertEqual((q.only_fields, q.sort.direction), (("msg",), SortDirection.ASC))

    def test_cursor_rejects_a_different_stream(self):
        first = self._first()
        with self.assertRaisesRegex(InvalidRequestError, "stream_id"):
            self.service.search_recent(
                text="level:3",
                stream_id="s2",
                seconds=3600,
                limit=2,
                next_cursor=first["next_cursor"],
            )

    def test_cursor_from_another_tool_is_rejected(self):
        self.store.stream_list = [Stream(f"s{i}", "t", None, False, False) for i in range(3)]
        streams = self.service.streams(limit=1)
        with self.assertRaisesRegex(InvalidRequestError, "different tool"):
            self.service.search_recent(
                text="*", stream_id="s1", seconds=60, limit=1, next_cursor=streams["next_cursor"]
            )

    def test_clamped_limit_is_reported_as_truncation(self):
        service = LogService(self.store, Limits(max_results=2), clock=lambda: NOW)
        first = service.search_recent(text="*", stream_id="s1", seconds=60, limit=10)
        self.assertEqual((first["limit"], first["has_more"], first["truncated"]), (2, True, True))
        self.assertIn("limit 10", first["truncation"])

        second = service.search_recent(
            text="*", stream_id="s1", seconds=60, limit=10, next_cursor=first["next_cursor"]
        )
        self.assertFalse(second["truncated"])
        self.assertNotIn("truncation", second)

    def test_no_truncation_when_everything_fits(self):
        service = LogService(self.store, Limits(max_results=2), clock=lambda: NOW)
        self.store.entries = self.store.entries[:2]
        result = service.search_recent(text="*", stream_id="s1", seconds=60, limit=10)
        self.assertEqual((result["has_more"], result["truncated"]), (False, False))

    def test_paging_stops_at_the_result_window(self):
        service = LogService(self.store, Limits(max_result_window=4), clock=lambda: NOW)
        first = service.search_recent(text="*", stream_id="s1", seconds=60, limit=2)
        second = service.search_recent(
            text="*", stream_id="s1", seconds=60, limit=2, next_cursor=first["next_cursor"]
        )
        self.assertEqual((first["has_more"], first["truncated"]), (True, False))
        self.assertEqual((second["has_more"], second["next_cursor"]), (False, None))
        self.assertTrue(second["truncated"])
        self.assertIn("first 4 of 5", second["truncation"])

    def test_edited_cursor_cannot_raise_the_limit(self):
        first = self._first()
        payload = json.loads(base64.urlsafe_b64decode(first["next_cursor"] + "=="))
        payload["request"]["limit"] = 10**6
        self._first(next_cursor=_cursor(payload))
        self.assertEqual(self.store.last_query.max_results, Limits().max_results)

    def test_edited_cursor_values_are_rejected(self):
        payload = json.loads(base64.urlsafe_b64decode(self._first()["next_cursor"] + "=="))
        for name, bad in (("sort_field", 3), ("window", {"from": "x"})):
            edited = json.loads(json.dumps(payload))
            edited["request"][name] = bad
            with self.subTest(name=name), self.assertRaisesRegex(InvalidRequestError, "invalid"):
                self._first(next_cursor=_cursor(edited))

    def test_empty_page_ends_paging(self):
        self.store.entries = []
        self.store.total_hits = 5
        result = self._first()
        self.assertEqual((result["has_more"], result["next_cursor"]), (False, None))


class MessageTest(ServiceTestCase):
    def test_fetches_and_cleans(self):
        self.store.stored[("idx_1", "m1")] = {"_id": "m1", "msg": "x"}
        self.assertEqual(self.service.message(index=" idx_1 ", message_id="m1"), {"msg": "x"})

    def test_requires_ids(self):
        with self.assertRaises(InvalidRequestError):
            self.service.message(index="", message_id="m1")

    def test_not_found_propagates(self):
        with self.assertRaises(NotFoundError):
            self.service.message(index="idx", message_id="nope")


class TopValuesTest(ServiceTestCase):
    def test_ranks_sample_and_requests_only_needed_fields(self):
        self.store.entries = [{"pod": "a"}, {"pod": "b"}, {"pod": "a"}]
        result = self.service.top_values(text="*", stream_id="s1", field="pod", seconds=60, top=5)
        self.assertEqual(
            result["top_values"], [{"value": "a", "count": 2}, {"value": "b", "count": 1}]
        )
        self.assertEqual(self.store.last_query.only_fields, ("timestamp", "pod"))
        self.assertEqual(self.store.last_query.max_results, Limits().aggregation_sample)
        self.assertNotIn("note", result)

    def test_note_when_sample_is_partial(self):
        self.store.entries = [{"pod": "a"}]
        self.store.total_hits = 5000
        result = self.service.top_values(text="*", stream_id="s1", field="pod", seconds=60, top=5)
        self.assertIn("5000", result["note"])

    def test_blank_field_rejected(self):
        with self.assertRaises(InvalidRequestError):
            self.service.top_values(text="*", stream_id="s1", field=" ", seconds=60, top=5)


class DiscoverFieldsTest(ServiceTestCase):
    def test_union_across_sample(self):
        self.store.entries = [{"a": 1}, {"b": 2, "a": 3}]
        result = self.service.discover_fields(stream_id="s1", seconds=60)
        self.assertEqual(result["fields"], ["a", "b"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(self.store.last_query.text, "*")

    def test_stream_required(self):
        with self.assertRaises(InvalidRequestError):
            self.service.discover_fields(stream_id="", seconds=60)


if __name__ == "__main__":
    unittest.main()
