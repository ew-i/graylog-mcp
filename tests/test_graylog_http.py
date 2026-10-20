import json
import unittest

import httpx

from graylog_mcp.domain.capabilities import Capability
from graylog_mcp.domain.errors import (
    AccessDeniedError,
    BackendError,
    BackendUnavailableError,
    NotFoundError,
    UnsupportedError,
)
from graylog_mcp.domain.models import Between, LastSeconds, LogQuery, Sort, SortDirection
from graylog_mcp.infrastructure.config import Settings
from graylog_mcp.infrastructure.graylog_api import (
    GraylogApi,
    first_list,
    integer,
    number,
    objects,
    text,
    timerange_json,
    total_count,
)
from graylog_mcp.infrastructure.graylog_capabilities import GraylogCapabilities
from graylog_mcp.infrastructure.graylog_http import (
    DEFAULT_STREAM_ID,
    GraylogStore,
    build_http_client,
)


class Recorder:
    """MockTransport handler that records requests and replies from a script."""

    def __init__(self, reply):
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.reply(request)


def store_with(reply) -> tuple[GraylogStore, Recorder]:
    recorder = Recorder(reply)
    client = httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(recorder))
    return GraylogStore(client), recorder


def remembering_store_with(reply) -> tuple[GraylogStore, GraylogCapabilities, Recorder]:
    """A store whose capabilities remember what they learn for a minute."""
    recorder = Recorder(reply)
    client = httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(recorder))
    capabilities = GraylogCapabilities(client, ttl=60)
    return GraylogStore(client, capabilities), capabilities, recorder


def ok(body) -> callable:
    return lambda _req: httpx.Response(200, json=body)


class ReadEndpointsTest(unittest.TestCase):
    def test_given_system_reply_when_reading_cluster_info_then_maps_cluster_info(self):
        # Given
        store, rec = store_with(ok({"version": "6.1", "hostname": "h", "extra": 1}))

        # When
        info = store.cluster_info()

        # Then
        self.assertEqual((info.version, info.hostname, info.timezone), ("6.1", "h", None))
        self.assertEqual(rec.requests[0].url.path, "/api/system")

    def test_given_paginated_endpoint_when_paging_streams_then_graylog_pages_them(self):
        # Given
        body = {
            "total": 40,  # every stream, ignoring the query
            "pagination": {"total": 39, "count": 2, "page": 3, "per_page": 2},
            "elements": [
                {"id": "b", "title": "B", "disabled": True},
                {"title": "no id, skipped"},
                {"id": DEFAULT_STREAM_ID, "is_default": True},  # never sent; dropped anyway
            ],
        }
        store, rec = store_with(ok(body))

        # When
        page = store.streams(offset=4, limit=2)

        # Then
        self.assertEqual(rec.requests[0].url.path, "/api/streams/paginated")
        self.assertEqual(
            dict(rec.requests[0].url.params),
            {
                "page": "3",
                "per_page": "2",
                "query": f"-id:{DEFAULT_STREAM_ID}",
                "sort": "title",
                "order": "asc",
            },
        )
        self.assertEqual([(s.id, s.disabled) for s in page.items], [("b", True)])
        self.assertEqual((page.total, page.next_offset), (39, 6))

    def test_given_no_total_when_paging_streams_then_item_count_is_total(self):
        # Given
        store, _ = store_with(ok({"elements": [{"id": "a"}]}))

        # When
        page = store.streams(offset=0, limit=5)

        # Then
        self.assertEqual(page.total, 1)

    def test_given_no_paginated_endpoint_when_paging_streams_then_pages_them_locally(self):
        # Given
        def reply(request):
            if request.url.path == "/api/streams/paginated":
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "streams": [
                        {"id": DEFAULT_STREAM_ID, "is_default": True},
                        {"id": "a"},
                        {"id": "b"},
                        {"id": "c"},
                    ]
                },
            )

        store, _ = store_with(reply)

        # When
        page = store.streams(offset=1, limit=5)

        # Then
        self.assertEqual([s.id for s in page.items], ["b", "c"])
        self.assertEqual((page.total, page.next_offset), (3, 3))

    def test_given_known_unknown_and_malformed_ids_when_checking_streams_then_answers_without_calling_for_malformed_ids(self):
        # Given
        found = "6abbd6f9d55b345cd2488cfa"
        store, rec = store_with(lambda request: httpx.Response(200 if found in request.url.path else 404, json={}))

        # When / Then
        self.assertTrue(store.stream_exists(found))
        self.assertFalse(store.stream_exists("aaaaaaaaaaaaaaaaaaaaaaaa"))
        self.assertFalse(store.stream_exists("nope"))  # malformed: Graylog would answer 500
        self.assertEqual(len(rec.requests), 2)

    def test_given_graylog_7_message_shape_when_fetching_then_unwraps_the_message_fields(self):
        # Given: the shape returned by GET /api/messages/{index}/{id} on Graylog 7.
        body = {
            "index": "idx",
            "message": {
                "fields": {"_id": "m/1", "msg": "x", "sequence": 17},
                "id": "m/1",
                "field_names": ["_id", "msg", "sequence"],
                "journal_offset": -1,
            },
        }
        store, rec = store_with(ok(body))

        # When
        entry = store.fetch("idx", "m/1")

        # Then
        self.assertEqual(entry.fields, {"_id": "m/1", "msg": "x", "sequence": 17})
        self.assertEqual(entry.index, "idx")
        self.assertEqual(rec.requests[0].url.raw_path, b"/api/messages/idx/m%2F1")

    def test_given_flat_message_shape_when_fetching_then_uses_the_fields_as_is(self):
        # Given
        store, _ = store_with(ok({"message": {"_id": "m1", "msg": "x"}}))

        # When
        entry = store.fetch("idx", "m1")

        # Then
        self.assertEqual(entry.fields, {"_id": "m1", "msg": "x"})


class CapabilityAwareStoreTest(unittest.TestCase):
    def test_given_no_paginated_endpoint_when_listing_streams_twice_then_asks_for_it_only_once(self):
        # Given
        def reply(request):
            if request.url.path == "/api/streams":
                return httpx.Response(200, json={"streams": [{"id": "a"}]})
            return httpx.Response(404)

        store, capabilities, rec = remembering_store_with(reply)

        # When
        store.streams(offset=0, limit=5)
        page = store.streams(offset=0, limit=5)

        # Then
        self.assertEqual([s.id for s in page.items], ["a"])
        self.assertEqual(
            [r.url.path for r in rec.requests],
            ["/api/streams/paginated", "/api/streams", "/api/streams"],
        )
        self.assertFalse(capabilities.known(Capability.PAGINATED_STREAMS))

    def test_given_field_types_known_missing_when_reading_types_then_raises_unsupported_without_a_request(self):
        # Given
        store, capabilities, rec = remembering_store_with(ok([]))
        capabilities.record(Capability.FIELD_TYPES, False)

        # When
        with self.assertRaises(UnsupportedError):
            store.field_types("s1", LastSeconds(60))

        # Then
        self.assertEqual(rec.requests, [])

    def test_given_existing_api_answering_404_when_reading_types_then_raises_not_found(self):
        # Given: the probe (no streams) proves the route exists; the query's stream does not.
        store, _, _ = remembering_store_with(
            lambda request: httpx.Response(404 if json.loads(request.content)["streams"] else 200, json=[])
        )

        # When
        with self.assertRaises(NotFoundError) as raised:
            store.field_types("s1", LastSeconds(60))

        # Then
        self.assertNotIsInstance(raised.exception, UnsupportedError)


class FieldTypesTest(unittest.TestCase):
    def test_given_field_type_properties_when_reading_types_then_maps_properties_to_capabilities(self):
        # Given
        body = [
            {"name": "message", "type": {"type": "string", "properties": ["full-text-search"]}},
            {"name": "code", "type": {"type": "long", "properties": ["numeric", "enumerable"]}},
            {"name": "odd", "type": "not-an-object"},
            {"type": {"type": "string"}},  # no name: skipped
            "junk",
        ]
        store, rec = store_with(ok(body))

        # When
        fields = store.field_types("s1", LastSeconds(60))

        # Then
        self.assertEqual(rec.requests[0].url.path, "/api/views/fields")
        self.assertEqual(
            json.loads(rec.requests[0].content),
            {"streams": ["s1"], "timerange": {"type": "relative", "range": 60}},
        )
        self.assertEqual(
            [(f.name, f.type, f.groupable, f.numeric) for f in fields],
            [
                ("message", "string", False, False),
                ("code", "long", True, True),
                ("odd", None, False, False),
            ],
        )

    def test_given_no_field_types_endpoint_when_reading_types_then_raises_unsupported(self):
        # Given
        store, _ = store_with(lambda _req: httpx.Response(404))

        # When / Then
        with self.assertRaises(UnsupportedError):
            store.field_types("s1", LastSeconds(60))

    def test_given_non_list_reply_when_reading_types_then_raises_backend_error(self):
        # Given
        store, _ = store_with(ok({"fields": []}))

        # When
        with self.assertRaises(BackendError) as raised:
            store.field_types("s1", LastSeconds(60))

        # Then
        self.assertRegex(str(raised.exception), "expected a JSON array")


class ValidateQueryTest(unittest.TestCase):
    def test_given_parse_error_reply_when_validating_then_maps_status_and_problems(self):
        # Given
        body = {
            "status": "ERROR",
            "explanations": [
                {"error_type": "QUERY_PARSING_ERROR", "error_message": "Cannot parse", "begin_line": 1, "begin_column": 4},
                "junk",
            ],
        }
        store, rec = store_with(ok(body))

        # When
        found = store.validate("(a:b", "s1", LastSeconds(60))

        # Then
        self.assertEqual(rec.requests[0].url.path, "/api/search/validate")
        self.assertEqual(
            json.loads(rec.requests[0].content),
            {"query": "(a:b", "streams": ["s1"], "timerange": {"type": "relative", "range": 60}},
        )
        self.assertEqual(found.status, "ERROR")
        self.assertEqual(
            [(p.kind, p.message, p.line, p.column) for p in found.problems],
            [("QUERY_PARSING_ERROR", "Cannot parse", 1, 4)],
        )

    def test_given_no_validation_endpoint_when_validating_then_raises_unsupported(self):
        # Given
        store, _ = store_with(lambda _req: httpx.Response(404))

        # When / Then
        with self.assertRaises(UnsupportedError):
            store.validate("*", "s1", LastSeconds(60))

    def test_given_validation_known_missing_when_validating_then_raises_unsupported_without_a_request(self):
        # Given
        store, capabilities, rec = remembering_store_with(ok({}))
        capabilities.record(Capability.QUERY_VALIDATION, False)

        # When
        with self.assertRaises(UnsupportedError):
            store.validate("*", "s1", LastSeconds(60))

        # Then
        self.assertEqual(rec.requests, [])

    def test_given_existing_api_answering_404_when_validating_then_raises_not_found(self):
        # Given: the probe (no streams) proves the route exists; the query's stream does not.
        store, _, _ = remembering_store_with(
            lambda request: httpx.Response(404 if json.loads(request.content).get("streams") else 200, json={})
        )

        # When
        with self.assertRaises(NotFoundError) as raised:
            store.validate("*", "s1", LastSeconds(60))

        # Then
        self.assertNotIsInstance(raised.exception, UnsupportedError)


class SearchTest(unittest.TestCase):
    body = {
        "total_results": 42,
        "messages": [{"message": {"msg": "a"}}, {"message": {"msg": "b"}}],
    }

    def test_given_relative_query_when_searching_then_calls_relative_endpoint_with_params(self):
        # Given
        store, rec = store_with(ok(self.body))

        # When
        page = store.search(
            LogQuery(
                text="level:3",
                stream_id="s1",
                window=LastSeconds(300),
                max_results=7,
                sort=Sort("level", SortDirection.ASC),
                only_fields=("msg", "level"),
            )
        )

        # Then
        req = rec.requests[0]
        self.assertEqual(req.url.path, "/api/search/universal/relative")
        self.assertEqual(
            dict(req.url.params),
            {
                "query": "level:3",
                "limit": "7",
                "sort": "level:asc",
                "filter": "streams:s1",
                "range": "300",
                "fields": "msg,level",
            },
        )
        self.assertEqual(page.total_hits, 42)
        self.assertEqual([e.fields["msg"] for e in page.entries], ["a", "b"])

    def test_given_malformed_hit_when_searching_then_next_offset_counts_it(self):
        # Given
        body = {"total_results": 9, "messages": [{"message": {"msg": "a"}}, "junk"]}
        store, _ = store_with(ok(body))

        # When
        page = store.search(LogQuery(text="*", stream_id="s1", window=LastSeconds(60), max_results=2, offset=4))

        # Then
        self.assertEqual((len(page.entries), page.next_offset), (1, 6))

        # Given
        store, _ = store_with(ok({"total_results": 9}))

        # When
        page = store.search(LogQuery(text="*", stream_id="s1", window=LastSeconds(60), max_results=2))

        # Then
        self.assertEqual(page.next_offset, 0)

    def test_given_absolute_query_when_searching_then_calls_absolute_endpoint_with_params(self):
        # Given
        store, rec = store_with(ok(self.body))

        # When
        store.search(LogQuery(text="*", stream_id="s1", window=Between("t0", "t1"), max_results=1))

        # Then
        params = dict(rec.requests[0].url.params)
        self.assertEqual(rec.requests[0].url.path, "/api/search/universal/absolute")
        self.assertEqual((params["from"], params["to"]), ("t0", "t1"))
        self.assertNotIn("range", params)
        self.assertNotIn("fields", params)

    def test_given_offset_when_searching_absolute_window_then_both_are_sent(self):
        # Given
        store, rec = store_with(ok(self.body))

        # When
        store.search(
            LogQuery(
                text="*",
                stream_id="s1",
                window=Between("t0", "t1"),
                max_results=1,
                offset=4,
            )
        )

        # Then
        params = dict(rec.requests[0].url.params)
        self.assertEqual(params["offset"], "4")
        self.assertEqual((params["from"], params["to"]), ("t0", "t1"))


class ErrorMappingTest(unittest.TestCase):
    def assert_maps(self, reply, error_type):
        store, _ = store_with(reply)
        with self.assertRaises(error_type):
            store.cluster_info()

    def test_given_http_error_statuses_when_calling_then_maps_statuses_to_domain_errors(self):
        # Given
        cases = {
            401: AccessDeniedError,
            403: AccessDeniedError,
            404: NotFoundError,
            500: BackendError,
        }
        for status, error_type in cases.items():
            with self.subTest(status=status):
                # When / Then
                self.assert_maps(lambda _r, s=status: httpx.Response(s, text="nope"), error_type)

    def test_given_non_json_reply_when_calling_then_raises_backend_error(self):
        # When / Then
        self.assert_maps(lambda _r: httpx.Response(200, text="<html>"), BackendError)

    def test_given_json_array_where_object_expected_when_calling_then_raises_backend_error(self):
        # When / Then
        self.assert_maps(lambda _r: httpx.Response(200, content=json.dumps([1, 2])), BackendError)

    def test_given_refused_connection_when_calling_then_raises_backend_unavailable(self):
        # Given
        def boom(request):
            raise httpx.ConnectError("refused", request=request)

        # When / Then
        self.assert_maps(boom, BackendUnavailableError)

    def test_given_timeout_when_calling_then_raises_backend_unavailable(self):
        # Given
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        # When / Then
        self.assert_maps(slow, BackendUnavailableError)


class ClientFactoryTest(unittest.TestCase):
    def test_given_settings_when_building_client_then_sets_url_headers_timeout_and_auth(self):
        # Given
        settings = Settings(base_url="https://gl.example", api_token="tok", verify_tls=True, timeout_seconds=5).validated()

        # When
        with build_http_client(settings) as client:
            # Then
            self.assertEqual(str(client.base_url), "https://gl.example")
            self.assertEqual(client.headers["X-Requested-By"], "graylog-mcp")
            self.assertEqual(client.timeout.read, 5)
            request = client.build_request("GET", "/api/system")
            client.auth.auth_flow(request).__next__()
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))

    def test_given_raw_values_when_converting_then_converts_to_text_integer_or_number(self):
        # When / Then
        self.assertIsNone(text(None))
        self.assertEqual(text(3), "3")
        self.assertIsNone(integer(True))
        self.assertEqual(integer(" -4 "), -4)
        self.assertIsNone(integer("x"))
        self.assertEqual(number("1.5"), 1.5)
        self.assertIsNone(number(False))

    def test_given_response_variants_when_parsing_then_parses_totals_lists_and_timeranges(self):
        # When / Then
        self.assertEqual(total_count({"total": 3}, "total"), 3)
        self.assertEqual(total_count({"pagination": {"total": 4}}), 4)
        # The filtered pagination total wins over an unfiltered top-level total.
        self.assertEqual(total_count({"total": 9, "pagination": {"total": 4}}, "total"), 4)
        self.assertEqual(total_count({"total": 9, "pagination": {}}, "total"), 9)
        self.assertIsNone(total_count({"other": 1}, "total"))
        self.assertIsNone(number(object()))
        self.assertEqual(objects([{}, "x"]), [{}])
        self.assertEqual(objects("x"), [])
        self.assertEqual(first_list({"a": [1, "x"]}, ("missing", "a")), [])
        self.assertEqual(first_list({"a": [{"id": 1}]}, ("a",)), [{"id": 1}])
        self.assertEqual(first_list({}, ("missing",)), [])
        self.assertEqual(timerange_json(LastSeconds(2)), {"type": "relative", "range": 2})
        self.assertEqual(
            timerange_json(Between("a", "b")),
            {"type": "absolute", "from": "a", "to": "b"},
        )
        with self.assertRaises(TypeError):
            timerange_json(object())

        # Given
        recorder = Recorder(lambda _request: httpx.Response(204))
        api = GraylogApi(httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(recorder)))

        # When / Then
        self.assertEqual(api.get("/empty"), {})
        with self.assertRaises(BackendError):
            api.get_list("/empty")


if __name__ == "__main__":
    unittest.main()
