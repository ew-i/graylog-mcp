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

    def test_given_server_when_listing_resources_then_lists_documented_uris_and_types(self):
        # When
        resources_by_uri = listed(self.server)

        # Then
        self.assertEqual(set(resources_by_uri), CACHED_RESOURCES | {GUIDE})
        for uri in CACHED_RESOURCES:
            with self.subTest(uri=uri):
                self.assertEqual(resources_by_uri[uri].mime_type, "application/json")
                self.assertIn("Cached for up to 60 s.", resources_by_uri[uri].description)
        self.assertEqual(resources_by_uri[GUIDE].mime_type, "text/markdown")

    def test_given_server_when_listing_templates_then_lists_the_stream_fields_template(self):
        # When
        [template] = asyncio.run(self.server.list_resource_templates())

        # Then
        self.assertEqual(template.uri_template, FIELDS_TEMPLATE)
        self.assertEqual(template.mime_type, "application/json")

    def test_given_catalogue_data_when_reading_each_resource_then_returns_data_from_its_use_case(self):
        # When / Then
        self.assertEqual(read(self.server, "graylog://streams")["streams"][0]["id"], "s1")
        self.assertEqual(read(self.server, "graylog://saved-searches")["saved_searches"][0]["id"], "v1")
        self.assertEqual(read(self.server, "graylog://cluster/status")["version"], "6.1.0")
        self.assertEqual(read(self.server, "graylog://alerts/definitions")["definitions"], [])
        self.assertEqual(read(self.server, "graylog://config/index-sets")["index_sets"], [])
        self.assertEqual(read(self.server, "graylog://config/pipelines")["pipelines"], [])

    def test_given_stream_when_reading_fields_template_then_returns_fields_over_thirty_days(self):
        # When
        fields = read(self.server, "graylog://streams/s1/fields")

        # Then
        names = [f["name"] for f in fields["fields"]]
        self.assertEqual((fields["stream_id"], names), ("s1", ["level", "msg", "pod"]))
        self.assertEqual(fields["window"], {"relative_seconds": 30 * 24 * 3600})

    def test_given_query_guide_when_reading_then_returns_static_markdown(self):
        # When
        [contents] = asyncio.run(self.server.read_resource(GUIDE))

        # Then
        self.assertEqual(contents.mime_type, "text/markdown")
        self.assertIn("_exists_:", contents.content)
        self.assertIn("browse_streams", contents.content)

    def test_given_catalogue_when_reading_then_reports_paging_metadata(self):
        # When
        streams = read(self.server, "graylog://streams")

        # Then
        self.assertEqual((streams["limit"], streams["has_more"]), (200, False))


class ResourceCacheTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.services = ServiceBundle(*(Mock() for _ in range(6)))
        self.server = MCPServer("test")
        resources.register(self.server, self.services, resources.TtlCache(60, clock=lambda: self.now))

    def test_given_cached_read_when_reading_again_then_reuses_it_until_the_ttl_expires(self):
        # Given
        self.services.search.cluster_info.side_effect = [{"version": "1"}, {"version": "2"}]

        # When / Then
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})
        self.now = 59.9
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})
        self.now = 60.0
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "2"})
        self.assertEqual(self.services.search.cluster_info.call_count, 2)

    def test_given_two_streams_when_reading_fields_then_each_has_its_own_entry(self):
        # Given
        self.services.search.discover_fields.side_effect = lambda stream_id, seconds: {"stream_id": stream_id}

        # When / Then
        self.assertEqual(read(self.server, "graylog://streams/s1/fields"), {"stream_id": "s1"})
        self.assertEqual(read(self.server, "graylog://streams/s2/fields"), {"stream_id": "s2"})
        self.assertEqual(read(self.server, "graylog://streams/s1/fields"), {"stream_id": "s1"})
        self.assertEqual(self.services.search.discover_fields.call_count, 2)

    def test_given_expired_entry_when_caching_another_then_drops_the_expired_entry(self):
        # Given
        cache = resources.TtlCache(10, clock=lambda: self.now)
        cache.get("a", lambda: 1)
        self.now = 11

        # When
        cache.get("b", lambda: 2)

        # Then
        self.assertEqual(set(cache._entries), {"b"})

    def test_given_two_resources_when_reading_both_then_each_has_its_own_entry(self):
        # Given
        self.services.search.cluster_info.return_value = {"version": "1"}
        self.services.configuration.index_sets.return_value = {"total": 3}
        read(self.server, "graylog://cluster/status")

        # When
        index_sets = read(self.server, "graylog://config/index-sets")

        # Then
        self.assertEqual(index_sets, {"total": 3})

    def test_given_failing_read_when_reading_then_returns_error_payload_and_does_not_cache_it(self):
        # Given
        self.services.search.cluster_info.side_effect = [
            BackendUnavailableError("could not reach Graylog"),
            {"version": "1"},
        ]

        # When / Then
        self.assertEqual(
            read(self.server, "graylog://cluster/status"),
            {"error": "could not reach Graylog", "kind": "BackendUnavailable"},
        )
        self.assertEqual(read(self.server, "graylog://cluster/status"), {"version": "1"})

    def test_given_zero_seconds_when_reading_twice_then_nothing_is_cached(self):
        # Given
        server = MCPServer("test")
        resources.register(server, self.services, resources.TtlCache(0, clock=lambda: 0.0))
        self.services.search.cluster_info.side_effect = [{"version": "1"}, {"version": "2"}]

        # When / Then
        self.assertEqual(read(server, "graylog://cluster/status"), {"version": "1"})
        self.assertEqual(read(server, "graylog://cluster/status"), {"version": "2"})
        [resource] = [r for r in asyncio.run(server.list_resources()) if str(r.uri) == "graylog://cluster/status"]
        self.assertTrue(resource.description.endswith("Not cached."))

    def test_given_cache_seconds_when_building_server_then_descriptions_state_them(self):
        # Given
        server = build_server(ServiceBundle(*(Mock() for _ in range(6))), resource_cache_seconds=5)

        # When
        descriptions = {r.description for r in asyncio.run(server.list_resources()) if r.mime_type == "application/json"}

        # Then
        self.assertTrue(all(d.endswith("Cached for up to 5 s.") for d in descriptions))

    def test_given_secret_in_resource_data_when_reading_then_redacts_the_secret(self):
        # Given
        self.services.configuration.pipeline_rules.return_value = {"source": "token=abc123"}

        # When
        pipelines = read(self.server, "graylog://config/pipelines")

        # Then
        self.assertEqual(pipelines, {"source": "token=[REDACTED]"})


if __name__ == "__main__":
    unittest.main()
