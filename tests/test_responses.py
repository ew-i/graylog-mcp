import json
import os
import unittest
from unittest.mock import patch

from graylog_mcp.domain.errors import NotFoundError
from graylog_mcp.interface.responses import respond


def fail(exc):
    def action():
        raise exc

    return action


class ResponseErrorTest(unittest.TestCase):
    def test_expected_error_keeps_its_kind_without_logging(self):
        with self.assertNoLogs("graylog_mcp.interface.responses"):
            result = json.loads(respond(fail(NotFoundError("no such stream"))))
        self.assertEqual(result, {"error": "no such stream", "kind": "NotFound"})

    def test_unexpected_error_becomes_internal_payload_and_is_logged(self):
        with self.assertLogs("graylog_mcp.interface.responses", level="ERROR"):
            result = json.loads(respond(fail(KeyError("password=hunter2"))))
        self.assertEqual(result["kind"], "Internal")
        self.assertTrue(result["error"].startswith("Unexpected KeyError: "))
        self.assertNotIn("hunter2", result["error"])


class ResponseRedactionTest(unittest.TestCase):
    def test_all_default_sensitive_field_names_are_redacted(self):
        sensitive_keys = (
            "accesskey",
            "apikey",
            "authorization",
            "clientsecret",
            "cookie",
            "credential",
            "password",
            "passwd",
            "privatekey",
            "secret",
            "session",
            "sessionid",
            "sessiontoken",
            "token",
            "webhook",
        )

        result = json.loads(respond(lambda: {key: f"value-for-{key}" for key in sensitive_keys}))

        for key in sensitive_keys:
            with self.subTest(key=key):
                self.assertEqual(result[key], "[REDACTED]")

    def test_redacts_each_supported_labeled_secret_pattern(self):
        cases = (
            ("Authorization: Bearer bearer-value", "Authorization: Bearer [REDACTED]"),
            ("Authorization: Basic basic-value", "Authorization: Basic [REDACTED]"),
            ("access_key: access-value", "access_key: [REDACTED]"),
            ("api-key=api-value", "api-key=[REDACTED]"),
            ("client_secret='client-value'", "client_secret=[REDACTED]"),
            ('password="password-value"', "password=[REDACTED]"),
            ("passwd=passwd-value", "passwd=[REDACTED]"),
            ("refresh-token: refresh-value", "refresh-token: [REDACTED]"),
            ("secret='secret-value'", "secret=[REDACTED]"),
            ("token=token-value", "token=[REDACTED]"),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                result = json.loads(respond(lambda original=original: {"message": original}))
                self.assertEqual(result["message"], expected)

    def test_redacts_each_supported_known_token_prefix(self):
        tokens = (
            "AKIA1234567890123456",
            "github_pat_1234567890",
            "ghp_1234567890",
            "gho_1234567890",
            "ghu_1234567890",
            "ghs_1234567890",
            "ghr_1234567890",
            "xoxb-1234567890",
            "xoxa-1234567890",
            "xoxp-1234567890",
            "xoxr-1234567890",
            "xoxs-1234567890",
            "sk-1234567890123456",
        )

        for token in tokens:
            with self.subTest(token=token):
                result = json.loads(respond(lambda token=token: {"message": token}))
                self.assertEqual(result["message"], "[REDACTED]")

    def test_redacts_nested_fields_and_credential_patterns(self):
        payload = {
            "password": "plain-secret",
            "events": [
                {
                    "fields": {"session_token": "session-secret"},
                    "message": (
                        "api_key='inline-key' https://alice:password@example.test/path "
                        "ghp_1234567890abcdefghijklmnop"
                    ),
                }
            ],
            "pipeline": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            "tuple": ("Authorization: Basic credentials", "ordinary"),
            "safe": "ordinary text",
        }

        result = json.loads(respond(lambda: payload))

        self.assertEqual(result["password"], "[REDACTED]")
        self.assertEqual(result["events"][0]["fields"]["session_token"], "[REDACTED]")
        self.assertEqual(
            result["events"][0]["message"],
            "api_key=[REDACTED] https://alice:[REDACTED]@example.test/path [REDACTED]",
        )
        self.assertEqual(result["pipeline"], "[REDACTED]")
        self.assertEqual(result["tuple"], ["Authorization: Basic [REDACTED]", "ordinary"])
        self.assertEqual(result["safe"], "ordinary text")

    def test_custom_field_names_extend_built_in_redaction(self):
        with patch.dict(os.environ, {"MCP_REDACT_FIELDS": "employee_id, internal-note"}):
            result = json.loads(
                respond(lambda: {"employee_id": "e-123", "internal-note": "private"})
            )

        self.assertEqual(result, {"employee_id": "[REDACTED]", "internal-note": "[REDACTED]"})


if __name__ == "__main__":
    unittest.main()
