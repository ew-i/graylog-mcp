import unittest
from unittest.mock import create_autospec

from graylog_mcp.application.alerts import AlertService
from graylog_mcp.application.analytics import AnalyticsService
from graylog_mcp.application.configuration import ConfigurationService
from graylog_mcp.application.incident import IncidentService
from graylog_mcp.application.operations import OperationsService
from graylog_mcp.application.service import LogService
from graylog_mcp.domain.errors import UnsupportedError

from .fakes import error_sections


class IncidentServiceTest(unittest.TestCase):
    def setUp(self):
        # Autospec doubles reject calls the real services would reject (e.g. a renamed
        # keyword), so signature drift fails here instead of hiding behind _capture.
        self.search = create_autospec(LogService, instance=True)
        self.analytics = create_autospec(AnalyticsService, instance=True)
        self.alerts = create_autospec(AlertService, instance=True)
        self.operations = create_autospec(OperationsService, instance=True)
        self.configuration = create_autospec(ConfigurationService, instance=True)
        self.service = IncidentService(self.search, self.analytics, self.alerts, self.operations, self.configuration)

        self.search.search_recent.return_value = {
            "total_results": 4,
            "returned": 1,
            "messages": [
                {"message": "no reference"},
                {"message": "boom", "_index": "graylog_1", "_id": "m1"},
            ],
        }
        self.search.cluster_info.return_value = {"version": "6.1"}
        self.operations.cluster_nodes.return_value = {"healthy": 1}
        self.alerts.recent_alerts.return_value = {"total": 1}
        self.analytics.compare_windows.return_value = {"totals": {"status": "up"}}
        self.analytics.exact_field_counts.return_value = {"top_values": []}
        self.analytics.message_context.return_value = {"before": [], "after": []}
        self.configuration.stream_rules.return_value = {"stream_id": "s1"}
        self.configuration.pipeline_rules.return_value = {"total": 1}

    def test_given_healthy_services_when_investigating_then_returns_every_section_with_nearby_context(self):
        # Given: the healthy service answers from setUp

        # When
        result = self.service.investigate(
            query="level:3",
            stream_id="s1",
            range_seconds=600,
            baseline_offset_seconds=1200,
            alert_limit=4,
            message_limit=2,
            top_n=3,
            context_field="pod",
            context_before=1,
            context_after=2,
            context_window_seconds=90,
        )

        # Then
        self.assertEqual(error_sections(result), {})
        self.assertEqual(result["cluster_health"], {"status": {"version": "6.1"}, "nodes": {"healthy": 1}})
        self.assertEqual(
            result["representative_messages"]["messages"][1],
            {"message": "boom", "_index": "graylog_1", "_id": "m1"},
        )
        self.assertEqual(result["nearby_context"], {"before": [], "after": []})
        self.assertEqual(set(result["top_values"]), {"source", "service", "level", "http_response_code"})
        self.search.search_recent.assert_called_once_with(
            text="level:3",
            stream_id="s1",
            seconds=600,
            limit=2,
        )
        self.analytics.message_context.assert_called_once_with(
            index="graylog_1",
            message_id="m1",
            stream_id="s1",
            before=1,
            after=2,
            context_field="pod",
            window_seconds=90,
        )
        self.alerts.recent_alerts.assert_called_once_with(seconds=600, limit=4, alerts_only=True)

    def test_given_unsupported_capabilities_when_investigating_then_keeps_the_other_sections(self):
        # Given
        self.search.cluster_info.side_effect = UnsupportedError("cluster unavailable")
        self.search.search_recent.return_value = {"messages": []}
        self.configuration.stream_rules.side_effect = UnsupportedError("stream unavailable")

        # When
        result = self.service.investigate(query="*", stream_id="s1")

        # Then
        self.assertEqual(result["cluster_health"]["status"]["kind"], "Unsupported")
        self.assertEqual(result["configuration"]["stream"]["kind"], "Unsupported")
        self.assertEqual(result["recent_alerts"], {"total": 1})
        self.assertIsNone(result["nearby_context"])

    def test_given_unexpected_error_in_one_section_when_investigating_then_only_it_fails(self):
        # Given
        self.alerts.recent_alerts.side_effect = AttributeError("'list' object has no attribute")

        # When
        with self.assertLogs("graylog_mcp.application.incident", level="ERROR"):
            result = self.service.investigate(query="*", stream_id="s1")

        # Then
        self.assertEqual(
            result["recent_alerts"],
            {
                "error": "Unexpected AttributeError: 'list' object has no attribute",
                "kind": "Internal",
            },
        )
        self.assertEqual(result["configuration"]["pipelines"], {"total": 1})
        self.assertEqual(result["nearby_context"], {"before": [], "after": []})


if __name__ == "__main__":
    unittest.main()
