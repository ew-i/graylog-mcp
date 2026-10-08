import json
import unittest

import httpx

from graylog_mcp.domain.errors import (
    AccessDeniedError,
    BackendError,
    BackendUnavailableError,
    NotFoundError,
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
from graylog_mcp.infrastructure.graylog_http import GraylogStore, build_http_client


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


def ok(body) -> callable:
    return lambda _req: httpx.Response(200, json=body)


class ReadEndpointsTest(unittest.TestCase):
    def test_cluster_info(self):
        store, rec = store_with(ok({"version": "6.1", "hostname": "h", "extra": 1}))
        info = store.cluster_info()
        self.assertEqual((info.version, info.hostname, info.timezone), ("6.1", "h", None))
        self.assertEqual(rec.requests[0].url.path, "/api/system")

    def test_streams_maps_flags(self):
        store, _ = store_with(
            ok(
                {
                    "streams": [
                        {"id": "a", "title": "A", "is_default": True},
                        {"id": "b", "title": "B", "disabled": True},
                        {"title": "no id, skipped"},
                    ]
                }
            )
        )
        streams = store.streams()
        self.assertEqual([s.id for s in streams], ["a", "b"])
        self.assertTrue(streams[0].is_default)
        self.assertTrue(streams[1].disabled)

    def test_fetch_unwraps_message_fields_and_escapes_path(self):
        # Shape returned by GET /api/messages/{index}/{id} on Graylog 7.
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
        entry = store.fetch("idx", "m/1")
        self.assertEqual(entry.fields, {"_id": "m/1", "msg": "x", "sequence": 17})
        self.assertEqual(entry.index, "idx")
        self.assertEqual(rec.requests[0].url.raw_path, b"/api/messages/idx/m%2F1")

    def test_fetch_accepts_flat_message(self):
        store, _ = store_with(ok({"message": {"_id": "m1", "msg": "x"}}))
        self.assertEqual(store.fetch("idx", "m1").fields, {"_id": "m1", "msg": "x"})


class SearchTest(unittest.TestCase):
    body = {
        "total_results": 42,
        "messages": [{"message": {"msg": "a"}}, {"message": {"msg": "b"}}],
    }

    def test_relative_search_params(self):
        store, rec = store_with(ok(self.body))
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

    def test_next_offset_counts_every_hit_sent_including_malformed(self):
        body = {"total_results": 9, "messages": [{"message": {"msg": "a"}}, "junk"]}
        store, _ = store_with(ok(body))
        page = store.search(
            LogQuery(text="*", stream_id="s1", window=LastSeconds(60), max_results=2, offset=4)
        )
        self.assertEqual((len(page.entries), page.next_offset), (1, 6))

        store, _ = store_with(ok({"total_results": 9}))
        page = store.search(
            LogQuery(text="*", stream_id="s1", window=LastSeconds(60), max_results=2)
        )
        self.assertEqual(page.next_offset, 0)

    def test_absolute_search_params(self):
        store, rec = store_with(ok(self.body))
        store.search(LogQuery(text="*", stream_id="s1", window=Between("t0", "t1"), max_results=1))
        params = dict(rec.requests[0].url.params)
        self.assertEqual(rec.requests[0].url.path, "/api/search/universal/absolute")
        self.assertEqual((params["from"], params["to"]), ("t0", "t1"))
        self.assertNotIn("range", params)
        self.assertNotIn("fields", params)

    def test_search_offset_does_not_replace_absolute_window(self):
        store, rec = store_with(ok(self.body))
        store.search(
            LogQuery(
                text="*",
                stream_id="s1",
                window=Between("t0", "t1"),
                max_results=1,
                offset=4,
            )
        )
        params = dict(rec.requests[0].url.params)
        self.assertEqual(params["offset"], "4")
        self.assertEqual((params["from"], params["to"]), ("t0", "t1"))


class ErrorMappingTest(unittest.TestCase):
    def assert_maps(self, reply, error_type):
        store, _ = store_with(reply)
        with self.assertRaises(error_type):
            store.cluster_info()

    def test_status_codes(self):
        cases = {
            401: AccessDeniedError,
            403: AccessDeniedError,
            404: NotFoundError,
            500: BackendError,
        }
        for status, error_type in cases.items():
            with self.subTest(status=status):
                self.assert_maps(lambda _r, s=status: httpx.Response(s, text="nope"), error_type)

    def test_non_json_body(self):
        self.assert_maps(lambda _r: httpx.Response(200, text="<html>"), BackendError)

    def test_non_object_body(self):
        self.assert_maps(lambda _r: httpx.Response(200, content=json.dumps([1, 2])), BackendError)

    def test_connection_error(self):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)

        self.assert_maps(boom, BackendUnavailableError)

    def test_timeout(self):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        self.assert_maps(slow, BackendUnavailableError)


class ClientFactoryTest(unittest.TestCase):
    def test_client_uses_settings(self):
        settings = Settings(
            base_url="https://gl.example", api_token="tok", verify_tls=True, timeout_seconds=5
        ).validated()
        with build_http_client(settings) as client:
            self.assertEqual(str(client.base_url), "https://gl.example")
            self.assertEqual(client.headers["X-Requested-By"], "graylog-mcp")
            self.assertEqual(client.timeout.read, 5)
            request = client.build_request("GET", "/api/system")
            client.auth.auth_flow(request).__next__()
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))

    def test_api_helpers_and_empty_response(self):
        self.assertIsNone(text(None))
        self.assertEqual(text(3), "3")
        self.assertIsNone(integer(True))
        self.assertEqual(integer(" -4 "), -4)
        self.assertIsNone(integer("x"))
        self.assertEqual(number("1.5"), 1.5)
        self.assertIsNone(number(False))

    def test_total_count_variants(self):
        self.assertEqual(total_count({"total": 3}, "total"), 3)
        self.assertEqual(total_count({"pagination": {"total": 4}}), 4)
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

        recorder = Recorder(lambda _request: httpx.Response(204))
        api = GraylogApi(
            httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(recorder))
        )
        self.assertEqual(api.get("/empty"), {})
        with self.assertRaises(BackendError):
            api.get_list("/empty")


if __name__ == "__main__":
    unittest.main()
