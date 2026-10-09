import asyncio
import json
import unittest
from unittest.mock import Mock

from mcp.server.mcpserver import MCPServer

from graylog_mcp.application.alerts import AlertService
from graylog_mcp.application.analytics import AnalyticsService
from graylog_mcp.application.configuration import ConfigurationService
from graylog_mcp.application.operations import OperationsService
from graylog_mcp.application.saved_views import SavedViewService
from graylog_mcp.application.service import LogService
from graylog_mcp.domain.errors import BackendUnavailableError
from graylog_mcp.domain.models import Stream
from graylog_mcp.domain.views import ViewSummary
from graylog_mcp.interface import resources
from graylog_mcp.interface.mcp_tools import ServiceBundle, build_server

from .fakes import (
    FakeAggregationStore,
    FakeAlertStore,
    FakeConfigStore,
    FakeLogStore,
    FakeSystemStore,
    FakeViewStore,
)

CACHED_RESOURCES = {
    "graylog://streams",
    "graylog://saved-searches",
    "graylog://cluster/status",
    "graylog://alerts/definitions",
    "graylog://config/index-sets",
    "graylog://config/pipelines",
}
GUIDE = "graylog://guide/query-syntax"
FIELDS_TEMPLATE = "graylog://streams/{stream_id}/fields"


def read(server: MCPServer, uri: str) -> dict:
    [contents] = asyncio.run(server.read_resource(uri))
    assert contents.mime_type == "application/json"
    return json.loads(contents.content)


def listed(server: MCPServer) -> dict:
    return {str(r.uri): r for r in asyncio.run(server.list_resources())}


class ResourceCatalogueTest(unittest.TestCase):
    def setUp(self):
        logs = FakeLogStore(stream_list=[Stream("s1", "api", None, False)])
        logs.entries = [{"msg": "a", "pod": "p1"}, {"msg": "b", "level": 3}]
        views = FakeViewStore(searches=[ViewSummary("v1", "Errors", None, None, None, None)])
        search = LogService(logs)
        self.server = build_server(
            ServiceBundle(
                search=search,
                analytics=AnalyticsService(logs, FakeAggregationStore()),
                alerts=AlertService(FakeAlertStore()),
                views=SavedViewService(views, search),
                operations=OperationsService(FakeSystemStore()),
                configuration=ConfigurationService(FakeConfigStore()),
            )
        )

    def test_registers_documented_resources(self):
        resources_by_uri = listed(self.server)
        self.assertEqual(set(resources_by_uri), CACHED_RESOURCES | {GUIDE})
        for uri in CACHED_RESOURCES:
            with self.subTest(uri=uri):
                self.assertEqual(resources_by_uri[uri].mime_type, "application/json")
                self.assertIn("Cached for up to 60 s.", resources_by_uri[uri].description)
        self.assertEqual(resources_by_uri[GUIDE].mime_type, "text/markdown")

    def test_registers_the_stream_fields_template(self):
        [template] = asyncio.run(self.server.list_resource_templates())
        self.assertEqual(template.uri_template, FIELDS_TEMPLATE)
        self.assertEqual(template.mime_type, "application/json")

    def test_every_resource_reads_its_use_case(self):
        self.assertEqual(read(self.server, "graylog://streams")["streams"][0]["id"], "s1")
        self.assertEqual(
            read(self.server, "graylog://saved-searches")["saved_searches"][0]["id"], "v1"
        )
        self.assertEqual(read(self.server, "graylog://cluster/status")["version"], "6.1.0")
        self.assertEqual(read(self.server, "graylog://alerts/definitions")["definitions"], [])
        self.assertEqual(read(self.server, "graylog://config/index-sets")["index_sets"], [])
        self.assertEqual(read(self.server, "graylog://config/pipelines")["pipelines"], [])

    def test_stream_fields_reads_discover_fields_for_that_stream(self):
        fields = read(self.server, "graylog://streams/s1/fields")
        self.assertEqual((fields["stream_id"], fields["fields"]), ("s1", ["level", "msg", "pod"]))
        self.assertEqual(fields["window"], {"relative_seconds": 30 * 24 * 3600})

    def test_query_guide_is_static_markdown(self):
        [contents] = asyncio.run(self.server.read_resource(GUIDE))
        self.assertEqual(contents.mime_type, "text/markdown")
        self.assertIn("_exists_:", contents.content)
        self.assertIn("browse_streams", contents.content)

    def test_catalogues_report_paging_metadata(self):
        streams = read(self.server, "graylog://streams")
        self.assertEqual((streams["limit"], streams["has_more"]), (200, False))


class ResourceCacheTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.services = ServiceBundle(*(Mock() for _ in range(6)))
        self.server = MCPServer("test")
        resources.register(
            self.server, self.services, resources.TtlCache(60, clock=lambda: self.now)
        )

    def test_reads_are_cached_until_the_ttl_expires(self):
        self.services.search.cluster_info.side_effect = [{"version": "1"}, {"version": "2"}]

        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})
        self.now = 59.9
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})
        self.now = 60.0
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "2"})
        self.assertEqual(self.services.search.cluster_info.call_count, 2)

    def test_each_stream_has_its_own_fields_entry(self):
        self.services.search.discover_fields.side_effect = lambda stream_id, seconds: {
            "stream_id": stream_id
        }
        self.assertEqual(read(self.server, "graylog://streams/s1/fields"), {"stream_id": "s1"})
        self.assertEqual(read(self.server, "graylog://streams/s2/fields"), {"stream_id": "s2"})
        self.assertEqual(read(self.server, "graylog://streams/s1/fields"), {"stream_id": "s1"})
        self.assertEqual(self.services.search.discover_fields.call_count, 2)

    def test_expired_entries_are_dropped(self):
        cache = resources.TtlCache(10, clock=lambda: self.now)
        cache.get("a", lambda: 1)
        self.now = 11
        cache.get("b", lambda: 2)
        self.assertEqual(set(cache._entries), {"b"})

    def test_each_resource_has_its_own_entry(self):
        self.services.search.cluster_info.return_value = {"version": "1"}
        self.services.configuration.index_sets.return_value = {"total": 3}

        read(self.server, "graylog://cluster/status")
        self.assertEqual(read(self.server, "graylog://config/index-sets"), {"total": 3})

    def test_failures_become_error_payloads_and_are_not_cached(self):
        self.services.search.cluster_info.side_effect = [
            BackendUnavailableError("could not reach Graylog"),
            {"version": "1"},
        ]

        self.assertEqual(
            read(self.server, "graylog://cluster/status"),
            {"error": "could not reach Graylog", "kind": "BackendUnavailable"},
        )
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})

    def test_zero_seconds_disables_caching(self):
        server = MCPServer("test")
        resources.register(server, self.services, resources.TtlCache(0, clock=lambda: 0.0))
        self.services.search.cluster_info.side_effect = [{"version": "1"}, {"version": "2"}]

        self.assertEqual(read(server, "graylog://cluster/status"), {"version": "1"})
        self.assertEqual(read(server, "graylog://cluster/status"), {"version": "2"})
        [resource] = [
            r
            for r in asyncio.run(server.list_resources())
            if str(r.uri) == "graylog://cluster/status"
        ]
        self.assertTrue(resource.description.endswith("Not cached."))

    def test_build_server_passes_the_cache_seconds(self):
        server = build_server(ServiceBundle(*(Mock() for _ in range(6))), resource_cache_seconds=5)
        descriptions = {
            r.description
            for r in asyncio.run(server.list_resources())
            if r.mime_type == "application/json"
        }
        self.assertTrue(all(d.endswith("Cached for up to 5 s.") for d in descriptions))

    def test_output_is_redacted(self):
        self.services.configuration.pipeline_rules.return_value = {"source": "token=abc123"}
        self.assertEqual(
            read(self.server, "graylog://config/pipelines"), {"source": "token=[REDACTED]"}
        )


if __name__ == "__main__":
    unittest.main()
