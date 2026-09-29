import unittest

from graylog_mcp.application.service import Limits, LogService, parse_field_list
from graylog_mcp.domain.errors import InvalidRequestError, NotFoundError
from graylog_mcp.domain.models import Between, LastSeconds, SortDirection, Stream

from .fakes import FakeLogStore


class ParseFieldListTest(unittest.TestCase):
    def test_trims_drops_blanks_and_dedupes(self):
        self.assertEqual(parse_field_list(" a, b,,a ,c "), ("a", "b", "c"))

    def test_empty(self):
        self.assertEqual(parse_field_list(""), ())


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.store = FakeLogStore()
        self.service = LogService(self.store)


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


class SearchTest(ServiceTestCase):
    def test_recent_search_builds_query_and_shapes_result(self):
        self.store.entries = [{"msg": "hi", "gl2_x": 1, "level": 3}]
        result = self.service.search_recent(
            text="level:3", stream_id="s1", seconds=120, limit=5, sort_order="ASC"
        )
        q = self.store.last_query
        self.assertEqual((q.text, q.stream_id, q.max_results), ("level:3", "s1", 5))
        self.assertEqual(q.window, LastSeconds(120))
        self.assertIs(q.sort.direction, SortDirection.ASC)
        self.assertEqual(result["messages"], [{"msg": "hi", "level": 3}])
        self.assertEqual(result["window"], {"relative_seconds": 120})
        self.assertEqual((result["total_results"], result["returned"]), (1, 1))

    def test_limits_are_clamped(self):
        service = LogService(self.store, Limits(max_results=10, max_window_seconds=100))
        service.search_recent(text="*", stream_id="s1", seconds=10**9, limit=10**9)
        self.assertEqual(self.store.last_query.max_results, 10)
        self.assertEqual(self.store.last_query.window, LastSeconds(100))

        service.search_recent(text="*", stream_id="s1", seconds=-5, limit=-5)
        self.assertEqual(self.store.last_query.max_results, 1)
        self.assertEqual(self.store.last_query.window, LastSeconds(1))

    def test_field_projection(self):
        self.store.entries = [{"msg": "hi", "level": 3, "pod": "p"}]
        result = self.service.search_recent(
            text="*", stream_id="s1", seconds=60, limit=5, fields="pod,msg"
        )
        self.assertEqual(result["messages"], [{"pod": "p", "msg": "hi"}])
        self.assertEqual(self.store.last_query.only_fields, ("pod", "msg"))

    def test_absolute_search(self):
        result = self.service.search_between(text="*", stream_id="s1", start="a", end="b", limit=5)
        self.assertEqual(self.store.last_query.window, Between("a", "b"))
        self.assertEqual(result["window"], {"from": "a", "to": "b"})

    def test_invalid_input_never_reaches_store(self):
        for kwargs in (
            {"text": " ", "stream_id": "s1"},
            {"text": "*", "stream_id": ""},
            {"text": "*", "stream_id": "s1", "sort_order": "up"},
        ):
            with self.subTest(**kwargs), self.assertRaises(InvalidRequestError):
                self.service.search_recent(seconds=60, limit=5, **kwargs)
        self.assertEqual(self.store.queries, [])


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
