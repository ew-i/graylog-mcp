import asyncio
import json
import unittest
from unittest.mock import Mock

from graylog_mcp.application.alerts import AlertService
from graylog_mcp.application.analytics import AnalyticsService
from graylog_mcp.application.capabilities import CapabilityService
from graylog_mcp.application.configuration import ConfigurationService
from graylog_mcp.application.operations import OperationsService
from graylog_mcp.application.saved_views import SavedViewService
from graylog_mcp.application.service import LogService
from graylog_mcp.domain.aggregation import AggregationRow, AggregationTable
from graylog_mcp.domain.configuration import StreamConfig
from graylog_mcp.domain.models import Stream
from graylog_mcp.domain.operations import InputNodeState, InputStatus
from graylog_mcp.interface.mcp_tools import ServiceBundle, build_server

from .fakes import (
    FakeAggregationStore,
    FakeAlertStore,
    FakeCapabilityStore,
    FakeConfigStore,
    FakeLogStore,
    FakeSystemStore,
    FakeViewStore,
    error_sections,
)

EXPECTED_TOOLS = {
    # search
    "cluster_status",
    "server_capabilities",
    "browse_streams",
    "find_recent_logs",
    "find_logs_between",
    "read_log_message",
    "rank_field_values",
    "discover_fields",
    # analytics
    "count_matches",
    "count_over_time",
    "exact_field_counts",
    "compare_windows",
    "field_stats",
    "message_context",
    # alerts
    "recent_alerts",
    "alert_definitions",
    # saved views
    "saved_searches",
    "run_saved_search",
    "dashboards",
    # operations
    "input_status",
    "throughput",
    "system_notifications",
    "cluster_nodes",
    # incident investigation
    "investigate_incident",
    # configuration
    "stream_rules",
    "pipeline_rules",
    "index_sets",
    "lookup_tables",
    "lookup_value",
}

STREAM_SCOPED = {
    "find_recent_logs",
    "find_logs_between",
    "rank_field_values",
    "discover_fields",
    "count_matches",
    "count_over_time",
    "exact_field_counts",
    "compare_windows",
    "field_stats",
    "message_context",
    "investigate_incident",
    "stream_rules",
}


def _text_of(result) -> str:
    if hasattr(result, "content"):
        return result.content[0].text
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


class McpToolsTest(unittest.TestCase):
    def setUp(self):
        self.logs = FakeLogStore(
            stream_list=[Stream("s1", "api", None, False)],
            entries=[{"msg": "boom", "pod": "a"}],
        )
        self.agg = FakeAggregationStore()
        self.system = FakeSystemStore()
        search = LogService(self.logs)
        self.server = build_server(
            ServiceBundle(
                search=search,
                analytics=AnalyticsService(self.logs, self.agg),
                alerts=AlertService(FakeAlertStore()),
                views=SavedViewService(FakeViewStore(), search),
                operations=OperationsService(self.system),
                configuration=ConfigurationService(
                    FakeConfigStore(streams={"s1": StreamConfig("s1", "api", None, "AND", False, None, ())})
                ),
                capabilities=CapabilityService(self.logs, FakeCapabilityStore()),
            )
        )

    def call(self, name: str, **args) -> dict:
        return json.loads(_text_of(asyncio.run(self.server.call_tool(name, args))))

    def tools(self):
        return {t.name: t for t in asyncio.run(self.server.list_tools())}

    def test_given_server_when_listing_tools_then_lists_expected_tools_with_default_log_level(self):
        # When
        tools = self.tools()

        # Then
        self.assertEqual(set(tools), EXPECTED_TOOLS)
        self.assertEqual(self.server.settings.log_level, "INFO")

    def test_given_server_when_listing_tools_then_every_tool_is_documented(self):
        # When
        tools = self.tools()

        # Then
        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertTrue(tool.description and len(tool.description) > 20)

    def test_given_stream_scoped_tools_when_listing_then_stream_id_is_required(self):
        # When
        tools = self.tools()

        # Then
        for name in STREAM_SCOPED:
            with self.subTest(tool=name):
                self.assertIn("stream_id", tools[name].input_schema["required"])

    def test_given_a_stream_when_browsing_streams_then_it_is_listed(self):
        # When
        result = self.call("browse_streams")

        # Then
        self.assertEqual(result["streams"][0]["id"], "s1")

    def test_given_real_services_when_asking_for_capabilities_then_reports_version_and_capabilities(self):
        # When
        result = self.call("server_capabilities")

        # Then
        self.assertEqual(result["version"], "6.1.0")
        self.assertEqual(result["unsupported_tools"], [])

    def test_given_real_services_when_investigating_then_no_section_is_an_error(self):
        # Given: real services, so a call that no longer fits a service's signature
        # becomes an Internal section here, which the error check catches.

        # When
        result = self.call("investigate_incident", query="level:3", stream_id="s1")

        # Then
        self.assertEqual((result["query"], result["stream_id"]), ("level:3", "s1"))
        self.assertEqual(error_sections(result), {})
        self.assertEqual(result["configuration"]["stream"]["title"], "api")

    def test_given_stored_messages_when_searching_then_returns_the_messages(self):
        # When
        result = self.call("find_recent_logs", query="*", stream_id="s1")

        # Then
        self.assertEqual(result["messages"], [{"msg": "boom", "pod": "a"}])

    def test_given_secrets_in_log_values_when_searching_then_they_are_redacted(self):
        # Given
        self.logs.entries = [
            {
                "password": "plain-secret",
                "nested": {"api_key": "key-value"},
                "message": "Authorization: Bearer bearer-value",
                "safe": "not secret",
            }
        ]

        # When
        result = self.call("find_recent_logs", query="*", stream_id="s1")

        # Then
        self.assertEqual(
            result["messages"],
            [
                {
                    "password": "[REDACTED]",
                    "nested": {"api_key": "[REDACTED]"},
                    "message": "Authorization: Bearer [REDACTED]",
                    "safe": "not secret",
                }
            ],
        )

    def test_given_messages_when_ranking_field_values_then_counts_each_value(self):
        # When
        result = self.call("rank_field_values", query="*", field="pod", stream_id="s1")

        # Then
        self.assertEqual(result["top_values"], [{"value": "a", "count": 1}])

    def test_given_a_sensitive_field_when_ranking_its_values_then_masks_each_value(self):
        # Given
        self.logs.entries = [{"session_id": "s-a"}, {"session_id": "s-a"}, {"session_id": "s-b"}]

        # When
        result = self.call("rank_field_values", query="*", field="session_id", stream_id="s1")

        # Then
        self.assertEqual(
            result["top_values"],
            [{"value": "[REDACTED-1]", "count": 2}, {"value": "[REDACTED-2]", "count": 1}],
        )

    def test_given_server_side_counts_when_counting_exactly_then_returns_the_counts(self):
        # Given
        self.agg.tables = [AggregationTable((AggregationRow(("a",), {"count": 42}),))]

        # When
        result = self.call("exact_field_counts", query="*", field="pod", stream_id="s1")

        # Then
        self.assertEqual(result["top_values"], [{"value": "a", "count": 42}])

    def test_given_boolean_argument_when_calling_tool_then_returns_only_problem_inputs(self):
        # Given
        self.system.input_list = [
            InputStatus("i1", "ok", "GELF", True, None, (InputNodeState("n1", "RUNNING", None, None),)),
            InputStatus("i2", "bad", "GELF", True, None, (InputNodeState("n1", "FAILED", None, None),)),
        ]

        # When
        result = self.call("input_status", only_problems=True)

        # Then
        self.assertEqual([i["id"] for i in result["inputs"]], ["i2"])

    def test_given_public_arguments_when_calling_each_tool_then_forwards_them_to_the_service(self):
        # Given
        search = Mock()
        analytics = Mock()
        alerts = Mock()
        views = Mock()
        operations = Mock()
        configuration = Mock()
        capabilities = Mock()
        server = build_server(ServiceBundle(search, analytics, alerts, views, operations, configuration, capabilities))
        cases = {
            "cluster_status": (search, "cluster_info", {}),
            "server_capabilities": (capabilities, "server_capabilities", {}),
            "browse_streams": (search, "streams", {"limit": 50, "next_cursor": ""}),
            "find_recent_logs": (
                search,
                "search_recent",
                {
                    "text": "level:error",
                    "stream_id": "s1",
                    "seconds": 123,
                    "limit": 7,
                    "fields": "source,message",
                    "sort_field": "source",
                    "sort_order": "asc",
                    "next_cursor": "",
                },
            ),
            "find_logs_between": (
                search,
                "search_between",
                {
                    "text": "level:error",
                    "stream_id": "s1",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T01:00:00Z",
                    "limit": 7,
                    "fields": "source,message",
                    "sort_field": "source",
                    "sort_order": "asc",
                    "next_cursor": "",
                },
            ),
            "read_log_message": (
                search,
                "message",
                {"index": "idx", "message_id": "msg", "fields": "source"},
            ),
            "rank_field_values": (
                search,
                "top_values",
                {"text": "*", "stream_id": "s1", "field": "source", "seconds": 123, "top": 3},
            ),
            "discover_fields": (search, "discover_fields", {"stream_id": "s1", "seconds": 123}),
            "count_matches": (
                analytics,
                "count_matches",
                {"text": "*", "stream_id": "s1", "seconds": 123},
            ),
            "count_over_time": (
                analytics,
                "count_over_time",
                {
                    "text": "*",
                    "stream_id": "s1",
                    "seconds": 123,
                    "interval": "1h",
                    "split_by": "source",
                    "split_limit": 3,
                },
            ),
            "exact_field_counts": (
                analytics,
                "exact_field_counts",
                {"text": "*", "stream_id": "s1", "field": "source", "seconds": 123, "top": 3},
            ),
            "compare_windows": (
                analytics,
                "compare_windows",
                {
                    "text": "*",
                    "stream_id": "s1",
                    "window_seconds": 123,
                    "baseline_offset_seconds": 456,
                    "field": "source",
                    "top": 3,
                },
            ),
            "field_stats": (
                analytics,
                "field_stats",
                {
                    "text": "*",
                    "stream_id": "s1",
                    "field": "duration",
                    "seconds": 123,
                    "percentiles": "50,95",
                    "group_by": "source",
                    "group_limit": 3,
                },
            ),
            "message_context": (
                analytics,
                "message_context",
                {
                    "index": "idx",
                    "message_id": "msg",
                    "stream_id": "s1",
                    "before": 2,
                    "after": 3,
                    "context_field": "source",
                    "window_seconds": 123,
                    "fields": "message",
                },
            ),
            "recent_alerts": (
                alerts,
                "recent_alerts",
                {
                    "seconds": 123,
                    "limit": 3,
                    "alerts_only": False,
                    "text": "error",
                    "definition_id": "def-1",
                    "next_cursor": "",
                },
            ),
            "alert_definitions": (
                alerts,
                "alert_definitions",
                {"text": "error", "limit": 3, "next_cursor": ""},
            ),
            "saved_searches": (
                views,
                "saved_searches",
                {"text": "error", "limit": 3, "next_cursor": ""},
            ),
            "run_saved_search": (
                views,
                "run_saved_search",
                {
                    "view_id": "view-1",
                    "stream_id": "s1",
                    "limit": 3,
                    "fields": "message",
                    "next_cursor": "",
                },
            ),
            "dashboards": (
                views,
                "dashboards",
                {"text": "ops", "limit": 3, "next_cursor": ""},
            ),
            "input_status": (operations, "input_status", {"only_problems": True}),
            "throughput": (operations, "throughput", {}),
            "system_notifications": (operations, "notifications", {}),
            "cluster_nodes": (operations, "cluster_nodes", {}),
            "stream_rules": (configuration, "stream_rules", {"stream_id": "s1"}),
            "pipeline_rules": (
                configuration,
                "pipeline_rules",
                {"stream_id": "s1", "include_source": False},
            ),
            "index_sets": (configuration, "index_sets", {"stream_id": "s1"}),
            "lookup_tables": (
                configuration,
                "lookup_tables",
                {"text": "hosts", "limit": 3, "next_cursor": ""},
            ),
            "lookup_value": (
                configuration,
                "lookup_value",
                {"table": "hosts", "key": "10.0.0.1"},
            ),
        }
        public_args = {
            "find_recent_logs": {
                "query": "level:error",
                "stream_id": "s1",
                "range_seconds": 123,
                "limit": 7,
                "fields": "source,message",
                "sort": "source",
                "sort_order": "asc",
            },
            "find_logs_between": {
                "query": "level:error",
                "stream_id": "s1",
                "from_time": "2026-01-01T00:00:00Z",
                "to_time": "2026-01-01T01:00:00Z",
                "limit": 7,
                "fields": "source,message",
                "sort": "source",
                "sort_order": "asc",
            },
            "rank_field_values": {
                "query": "*",
                "stream_id": "s1",
                "field": "source",
                "range_seconds": 123,
                "top_n": 3,
            },
            "discover_fields": {"stream_id": "s1", "range_seconds": 123},
            "count_matches": {"query": "*", "stream_id": "s1", "range_seconds": 123},
            "count_over_time": {
                "query": "*",
                "stream_id": "s1",
                "range_seconds": 123,
                "interval": "1h",
                "split_by": "source",
                "split_limit": 3,
            },
            "exact_field_counts": {
                "query": "*",
                "stream_id": "s1",
                "field": "source",
                "range_seconds": 123,
                "top_n": 3,
            },
            "compare_windows": {
                "query": "*",
                "stream_id": "s1",
                "window_seconds": 123,
                "baseline_offset_seconds": 456,
                "field": "source",
                "top_n": 3,
            },
            "field_stats": {
                "query": "*",
                "stream_id": "s1",
                "field": "duration",
                "range_seconds": 123,
                "percentiles": "50,95",
                "group_by": "source",
                "group_limit": 3,
            },
            "recent_alerts": {
                "range_seconds": 123,
                "limit": 3,
                "alerts_only": False,
                "query": "error",
                "definition_id": "def-1",
            },
            "alert_definitions": {"query": "error", "limit": 3},
            "saved_searches": {"query": "error", "limit": 3},
            "dashboards": {"query": "ops", "limit": 3},
            "lookup_tables": {"query": "hosts", "limit": 3},
        }

        for name, (service, method_name, _args) in cases.items():
            getattr(service, method_name).return_value = {"handled_by": name}

        for name, (service, method_name, args) in cases.items():
            with self.subTest(tool=name):
                # When
                result = json.loads(_text_of(asyncio.run(server.call_tool(name, public_args.get(name, args)))))

                # Then
                self.assertEqual(result, {"handled_by": name})
                getattr(service, method_name).assert_called_once_with(**args)

    def test_given_invalid_arguments_when_calling_tools_then_returns_invalid_request_payloads(self):
        # When
        result = self.call("find_recent_logs", query="*", stream_id=" ")

        # Then
        self.assertEqual(result["kind"], "InvalidRequest")
        self.assertIn("browse_streams", result["error"])

        # When
        result = self.call("count_over_time", query="*", stream_id="s1", interval="soon")

        # Then
        self.assertEqual(result["kind"], "InvalidRequest")

    def test_given_missing_resources_when_calling_tools_then_returns_not_found_payloads(self):
        # When / Then
        self.assertEqual(self.call("read_log_message", index="i", message_id="missing")["kind"], "NotFound")
        self.assertEqual(self.call("stream_rules", stream_id="nope")["kind"], "NotFound")
        self.assertEqual(self.call("run_saved_search", view_id="nope")["kind"], "NotFound")


if __name__ == "__main__":
    unittest.main()
