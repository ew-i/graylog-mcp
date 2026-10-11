import asyncio
import re
import unittest
from unittest.mock import Mock

from graylog_mcp.interface.mcp_tools import ServiceBundle, build_server
from graylog_mcp.interface.query_guide import QUERY_SYNTAX

EXPECTED_PROMPTS = {
    "triage-production-alert": set(),
    "investigate-service-errors": {"service"},
    "compare-deployment-impact": {"deployed_at"},
    "find-missing-logs": {"source"},
    "build-query": {"request"},
    "explain-log-message": {"index", "message_id"},
    "daily-briefing": set(),
    "summarize-cluster-health": set(),
}
SAMPLE_ARGUMENTS = {
    "investigate-service-errors": {"service": "checkout"},
    "compare-deployment-impact": {"deployed_at": "2026-10-01T10:00:00Z"},
    "find-missing-logs": {"source": "web-01"},
    "build-query": {"request": "5xx errors from checkout"},
    "explain-log-message": {"index": "graylog_0", "message_id": "m1"},
}


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.server = build_server(ServiceBundle(*(Mock() for _ in range(6))))
        self.tools = {t.name for t in asyncio.run(self.server.list_tools())}

    def messages(self, name: str, **arguments):
        messages = asyncio.run(self.server.get_prompt(name, arguments)).messages
        self.assertTrue(all(m.role == "user" for m in messages))
        return messages

    def render(self, name: str, **arguments) -> str:
        """The instruction text; embedded resources are left out."""
        texts = [m.content.text for m in self.messages(name, **arguments) if m.content.type == "text"]
        self.assertEqual(len(texts), 1)
        return texts[0]

    def test_given_server_when_listing_prompts_then_lists_documented_prompts_with_required_arguments(self):
        # When
        prompts = {p.name: p for p in asyncio.run(self.server.list_prompts())}

        # Then
        self.assertEqual(set(prompts), set(EXPECTED_PROMPTS))
        for name, prompt in prompts.items():
            with self.subTest(prompt=name):
                self.assertTrue(prompt.title and prompt.description)
                required = {a.name for a in prompt.arguments if a.required}
                self.assertEqual(required, EXPECTED_PROMPTS[name])
                self.assertTrue(all(a.description for a in prompt.arguments))

    def test_given_each_prompt_when_rendering_then_every_backticked_tool_exists(self):
        for name in EXPECTED_PROMPTS:
            with self.subTest(prompt=name):
                # When
                text = self.render(name, **SAMPLE_ARGUMENTS.get(name, {}))

                # Then
                named = set(re.findall(r"`([^`]+)`", text))
                self.assertTrue(named, "a prompt should name the tools to call")
                self.assertLessEqual(named, self.tools)

    def test_given_each_prompt_when_rendering_then_never_groups_by_full_text_message(self):
        # Graylog rejects server-side grouping on `message`.
        for name in EXPECTED_PROMPTS:
            with self.subTest(prompt=name):
                # When
                text = self.render(name, **SAMPLE_ARGUMENTS.get(name, {}))

                # Then
                self.assertNotIn("set to the message", text)
                self.assertIn("`rank_field_values`", text)

    def test_given_all_arguments_when_rendering_then_they_are_filled_in(self):
        # When
        text = self.render("investigate-service-errors", service="checkout", stream_id="s1", range_seconds="900")

        # Then
        self.assertIn('"checkout"', text)
        self.assertIn('Use stream_id "s1".', text)
        self.assertIn("range_seconds=900", text)
        self.assertNotIn("browse_streams", text)

    def test_given_no_stream_when_rendering_then_browse_streams_is_suggested(self):
        # When / Then
        self.assertIn("call `browse_streams`", self.render("find-missing-logs", source="web-01"))
        self.assertIn("call `browse_streams`", self.render("daily-briefing"))
        self.assertNotIn("brief on the streams", self.render("daily-briefing", stream_id="s1"))

    def test_given_request_when_rendering_build_query_then_attaches_the_query_guide_first(self):
        # When
        guide, steps = self.messages("build-query", request="5xx errors from checkout")

        # Then
        self.assertEqual(guide.content.type, "resource")
        self.assertEqual(str(guide.content.resource.uri), "graylog://guide/query-syntax")
        self.assertEqual(guide.content.resource.text, QUERY_SYNTAX)
        self.assertIn('"5xx errors from checkout"', steps.content.text)

    def test_given_no_stream_when_rendering_explain_message_then_reads_the_stream_from_the_message(self):
        # When
        text = self.render("explain-log-message", index="graylog_0", message_id="m1")
        given = self.render("explain-log-message", index="i", message_id="m", stream_id="s1")

        # Then
        self.assertIn('_index "graylog_0" and _id "m1"', text)
        self.assertIn('fields set to "streams"', text)
        self.assertIn('Use stream_id "s1".', given)

    def test_given_optional_arguments_when_rendering_then_wording_follows_them(self):
        # When / Then
        self.assertIn("the newest alert", self.render("triage-production-alert"))
        self.assertIn(
            'the alert matching "disk full"',
            self.render("triage-production-alert", alert="disk full"),
        )
        self.assertIn(
            "all services in the stream",
            self.render("compare-deployment-impact", deployed_at="2026-10-01T10:00:00Z"),
        )
        self.assertIn(
            'for "api"',
            self.render("compare-deployment-impact", deployed_at="2026-10-01T10:00:00Z", service="api"),
        )

    def test_given_missing_or_invalid_arguments_when_rendering_then_rejects_them(self):
        # When
        with self.assertRaises(ValueError) as raised:
            asyncio.run(self.server.get_prompt("investigate-service-errors", {}))

        # Then
        self.assertRegex(str(raised.exception), "Missing required arguments")

        # When / Then
        with self.assertRaises(ValueError):
            asyncio.run(self.server.get_prompt("investigate-service-errors", {"service": "x", "range_seconds": "soon"}))


if __name__ == "__main__":
    unittest.main()
