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
        texts = [
            m.content.text for m in self.messages(name, **arguments) if m.content.type == "text"
        ]
        self.assertEqual(len(texts), 1)
        return texts[0]

    def test_registers_documented_prompts_with_required_arguments(self):
        prompts = {p.name: p for p in asyncio.run(self.server.list_prompts())}
        self.assertEqual(set(prompts), set(EXPECTED_PROMPTS))
        for name, prompt in prompts.items():
            with self.subTest(prompt=name):
                self.assertTrue(prompt.title and prompt.description)
                required = {a.name for a in prompt.arguments if a.required}
                self.assertEqual(required, EXPECTED_PROMPTS[name])
                self.assertTrue(all(a.description for a in prompt.arguments))

    def test_every_named_tool_exists(self):
        for name in EXPECTED_PROMPTS:
            with self.subTest(prompt=name):
                text = self.render(name, **SAMPLE_ARGUMENTS.get(name, {}))
                named = set(re.findall(r"`([^`]+)`", text))
                self.assertTrue(named, "a prompt should name the tools to call")
                self.assertLessEqual(named, self.tools)

    def test_no_prompt_groups_by_the_full_text_message_field(self):
        # Graylog rejects server-side grouping on `message`.
        for name in EXPECTED_PROMPTS:
            with self.subTest(prompt=name):
                text = self.render(name, **SAMPLE_ARGUMENTS.get(name, {}))
                self.assertNotIn("set to the message", text)
                self.assertIn("`rank_field_values`", text)

    def test_arguments_are_filled_in(self):
        text = self.render(
            "investigate-service-errors", service="checkout", stream_id="s1", range_seconds="900"
        )
        self.assertIn('"checkout"', text)
        self.assertIn('Use stream_id "s1".', text)
        self.assertIn("range_seconds=900", text)
        self.assertNotIn("browse_streams", text)

    def test_missing_stream_asks_for_browse_streams(self):
        self.assertIn("call `browse_streams`", self.render("find-missing-logs", source="web-01"))
        self.assertIn("call `browse_streams`", self.render("daily-briefing"))
        self.assertNotIn("brief on the streams", self.render("daily-briefing", stream_id="s1"))

    def test_build_query_attaches_the_query_guide(self):
        guide, steps = self.messages("build-query", request="5xx errors from checkout")
        self.assertEqual(guide.content.type, "resource")
        self.assertEqual(str(guide.content.resource.uri), "graylog://guide/query-syntax")
        self.assertEqual(guide.content.resource.text, QUERY_SYNTAX)
        self.assertIn('"5xx errors from checkout"', steps.content.text)

    def test_explain_log_message_finds_its_stream_when_none_is_given(self):
        text = self.render("explain-log-message", index="graylog_0", message_id="m1")
        self.assertIn('_index "graylog_0" and _id "m1"', text)
        self.assertIn('fields set to "streams"', text)
        given = self.render("explain-log-message", index="i", message_id="m", stream_id="s1")
        self.assertIn('Use stream_id "s1".', given)

    def test_optional_arguments_change_the_wording(self):
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
            self.render(
                "compare-deployment-impact", deployed_at="2026-10-01T10:00:00Z", service="api"
            ),
        )

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing required arguments"):
            asyncio.run(self.server.get_prompt("investigate-service-errors", {}))
        with self.assertRaises(ValueError):
            asyncio.run(
                self.server.get_prompt(
                    "investigate-service-errors", {"service": "x", "range_seconds": "soon"}
                )
            )


if __name__ == "__main__":
    unittest.main()
