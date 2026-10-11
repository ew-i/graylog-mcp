import json
import unittest

from graylog_mcp.domain.errors import NotFoundError
from graylog_mcp.interface.redaction import Redactor
from graylog_mcp.interface.responses import Responder

respond = Responder(Redactor())


def fail(exc):
    def action():
        raise exc

    return action


def redacted_message(message, redactor=None):
    responder = Responder(redactor) if redactor else respond
    return json.loads(responder(lambda: {"message": message}))["message"]


class ResponseErrorTest(unittest.TestCase):
    def test_given_expected_error_when_responding_then_keeps_the_kind_and_logs_nothing(self):
        # Given
        action = fail(NotFoundError("no such stream"))

        # When
        with self.assertNoLogs("graylog_mcp.interface.responses"):
            result = json.loads(respond(action))

        # Then
        self.assertEqual(result, {"error": "no such stream", "kind": "NotFound"})

    def test_given_unexpected_error_when_responding_then_logs_it_and_returns_a_redacted_internal_error(self):
        # Given
        action = fail(KeyError("password=hunter2"))

        # When
        with self.assertLogs("graylog_mcp.interface.responses", level="ERROR"):
            result = json.loads(respond(action))

        # Then
        self.assertEqual(result["kind"], "Internal")
        self.assertTrue(result["error"].startswith("Unexpected KeyError: "))
        self.assertNotIn("hunter2", result["error"])


class ResponseRedactionTest(unittest.TestCase):
    def test_given_default_sensitive_field_names_when_responding_then_redacts_their_values(self):
        # Given
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

        # When
        result = json.loads(respond(lambda: {key: f"value-for-{key}" for key in sensitive_keys}))

        # Then
        for key in sensitive_keys:
            with self.subTest(key=key):
                self.assertEqual(result[key], "[REDACTED]")

    def test_given_keys_containing_a_sensitive_word_when_responding_then_redacts_their_values(self):
        # Given
        sensitive_keys = (
            "credentials",
            "password_hash",
            "api_key_id",
            "secret_value",
            "auth",
            "oauth",
            "x-api-key",
            "private_key_pem",
            "AccountKey",
            "user.passphrase",
            "db_pwd",
            "request_signature",
            "webhook_url",
        )

        # When
        result = json.loads(respond(lambda: dict.fromkeys(sensitive_keys, "value")))

        # Then
        for key in sensitive_keys:
            with self.subTest(key=key):
                self.assertEqual(result[key], "[REDACTED]")

    def test_given_keys_that_describe_a_secret_when_responding_then_keeps_their_values(self):
        # Given
        payload = {
            "author": "jane",
            "session_duration": 12,
            "token_count": 5,
            "total_tokens": 9,
            "password_length": 12,
            "token_type": "Bearer",
            "token_expires_at": "2026-01-01",
            "next_cursor": "c1",
            "_id": "m1",
            "stream_id": "s1",
        }

        # When
        result = json.loads(respond(lambda: payload))

        # Then
        self.assertEqual(result, payload)

    def test_given_flags_or_nulls_under_sensitive_keys_when_responding_then_keeps_them(self):
        # Given
        payload = {"password_expired": True, "has_token": False, "secret": None, "token": 0}

        # When
        result = json.loads(respond(lambda: payload))

        # Then
        self.assertEqual(result, {"password_expired": True, "has_token": False, "secret": None, "token": "[REDACTED]"})

    def test_given_labelled_secrets_in_text_when_responding_then_redacts_only_the_values(self):
        # Given
        cases = (
            ("Authorization: Bearer bearer-value", "Authorization: Bearer [REDACTED]"),
            ("Authorization: Basic basic-value", "Authorization: Basic [REDACTED]"),
            ("Authorization: Token token-value", "Authorization: Token [REDACTED]"),
            ("Proxy-Authorization: raw-value", "Proxy-Authorization: [REDACTED]"),
            ('"authorization": "Digest digest-value"', '"authorization": "Digest [REDACTED]"'),
            ("access_key: access-value", "access_key: [REDACTED]"),
            ("api-key=api-value", "api-key=[REDACTED]"),
            ("client_secret='client-value'", "client_secret='[REDACTED]'"),
            ('password="password-value"', 'password="[REDACTED]"'),
            ("passwd=passwd-value", "passwd=[REDACTED]"),
            ("refresh-token: refresh-value", "refresh-token: [REDACTED]"),
            ("secret='secret-value'", "secret='[REDACTED]'"),
            ("token=token-value", "token=[REDACTED]"),
            ("accessToken=camel-value", "accessToken=[REDACTED]"),
            ("passphrase: phrase-value", "passphrase: [REDACTED]"),
            ("Uid=sa;Pwd=odbc-value;", "Uid=sa;Pwd=[REDACTED];"),
            ("AccountName=a;AccountKey=azure-value==;", "AccountName=a;AccountKey=[REDACTED];"),
            ("SharedAccessSignature=sas-value", "SharedAccessSignature=[REDACTED]"),
            ('{"password": "json-value", "user": "bob"}', '{"password": "[REDACTED]", "user": "bob"}'),
            ('{"password":"esc\\"aped","user":"bob"}', '{"password":"[REDACTED]","user":"bob"}'),
            ("GET /cb?token=query-value&user=bob&page=2", "GET /cb?token=[REDACTED]&user=bob&page=2"),
            ("/blob?sv=2020&sig=sas%2Bvalue&se=1", "/blob?sv=2020&sig=[REDACTED]&se=1"),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                # When
                result = redacted_message(original)

                # Then
                self.assertEqual(result, expected)

    def test_given_labels_inside_other_words_when_responding_then_keeps_the_text(self):
        # Given
        texts = ("secretary=bob", "tokens=5", "the bearer of bad news", "author: jane")

        for text in texts:
            with self.subTest(text=text):
                # When
                result = redacted_message(text)

                # Then
                self.assertEqual(result, text)

    def test_given_known_token_prefixes_when_responding_then_redacts_the_tokens(self):
        # Given
        tokens = (
            "AKIA1234567890123456",
            "ASIA1234567890123456",
            "AIzaSyA-1234567890abcdefghijklmnopqrstu",
            "github_pat_1234567890",
            "ghp_1234567890",
            "gho_1234567890",
            "ghu_1234567890",
            "ghs_1234567890",
            "ghr_1234567890",
            "glpat-abcdefghij1234567890",
            "xoxb-1234567890",
            "xoxa-1234567890",
            "xoxe-1234567890",
            "xoxp-1234567890",
            "xoxr-1234567890",
            "xoxs-1234567890",
            "sk-1234567890123456",
            "sk-proj-AbCdEfGhIjKlMnOpQrStUv",
            "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv-wxyz",
            "sk_live_1234567890abcdef",
            "rk_test_1234567890abcdef",
            "npm_" + "a" * 36,
            "pypi-" + "a" * 50,
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature_abc-DEF",
        )

        for token in tokens:
            with self.subTest(token=token):
                # When
                result = redacted_message(token)

                # Then
                self.assertEqual(result, "[REDACTED]")

    def test_given_a_bare_bearer_token_when_responding_then_redacts_the_token(self):
        # When
        result = redacted_message("sent Bearer abcdefghijklmnop0123 upstream")

        # Then
        self.assertEqual(result, "sent Bearer [REDACTED] upstream")

    def test_given_credentials_in_urls_when_responding_then_redacts_the_passwords(self):
        # Given
        cases = (
            ("https://alice:pw@example.test/path", "https://alice:[REDACTED]@example.test/path"),
            ("postgres://app:s3cr3t@db:5432/x", "postgres://app:[REDACTED]@db:5432/x"),
            ("mongodb+srv://u:p@cluster/db", "mongodb+srv://u:[REDACTED]@cluster/db"),
            ("redis://:s3cr3t@cache:6379", "redis://:[REDACTED]@cache:6379"),
            ("http://host:8080/p?email=a@b.test", "http://host:8080/p?email=a@b.test"),
            ("https://token:pw@host/x", "https://token:[REDACTED]@host/x"),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                # When
                result = redacted_message(original)

                # Then
                self.assertEqual(result, expected)

    def test_given_webhook_urls_when_responding_then_redacts_their_secret_paths(self):
        # Given
        cases = (
            ("https://hooks.slack.com/services/T0/B0/XX said hi", "https://hooks.slack.com/services/[REDACTED] said hi"),
            ("https://discord.com/api/webhooks/123/abc", "https://discord.com/api/webhooks/[REDACTED]"),
            (
                "post to https://contoso.webhook.office.com/webhookb2/a-1@t-2/IncomingWebhook/c3/u-4/s5 failed",
                "post to https://contoso.webhook.office.com/webhookb2/[REDACTED] failed",
            ),
            ("https://outlook.office.com/webhook/a-1@t-2/IncomingWebhook/c3", "https://outlook.office.com/webhook/[REDACTED]"),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                # When
                result = redacted_message(original)

                # Then
                self.assertEqual(result, expected)

    def test_given_a_truncated_private_key_when_responding_then_redacts_to_the_end(self):
        # When
        result = redacted_message("key: -----BEGIN RSA PRIVATE KEY-----\nMIIabc (truncated")

        # Then
        self.assertEqual(result, "key: [REDACTED]")

    def test_given_secrets_in_mapping_keys_when_responding_then_redacts_the_keys(self):
        # When
        result = json.loads(respond(lambda: {"counts": {"ghp_1234567890": 1}}))

        # Then
        self.assertEqual(result, {"counts": {"[REDACTED]": 1}})

    def test_given_nested_secrets_when_responding_then_redacts_them_at_every_depth(self):
        # Given
        payload = {
            "password": "plain-secret",
            "events": [
                {
                    "fields": {"session_token": "session-secret"},
                    "message": ("api_key='inline-key' https://alice:password@example.test/path ghp_1234567890abcdefghijklmnop"),
                }
            ],
            "pipeline": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            "tuple": ("Authorization: Basic credentials", "ordinary"),
            "safe": "ordinary text",
        }

        # When
        result = json.loads(respond(lambda: payload))

        # Then
        self.assertEqual(result["password"], "[REDACTED]")
        self.assertEqual(result["events"][0]["fields"]["session_token"], "[REDACTED]")
        self.assertEqual(
            result["events"][0]["message"],
            "api_key='[REDACTED]' https://alice:[REDACTED]@example.test/path [REDACTED]",
        )
        self.assertEqual(result["pipeline"], "[REDACTED]")
        self.assertEqual(result["tuple"], ["Authorization: Basic [REDACTED]", "ordinary"])
        self.assertEqual(result["safe"], "ordinary text")


class ConfiguredRedactionTest(unittest.TestCase):
    def test_given_custom_redact_fields_when_responding_then_redacts_exact_matches_only(self):
        # Given
        redactor = Redactor(fields=["employee_id", " internal-note ", ""])
        payload = {"employee_id": "e-123", "Internal_Note": "private", "manager_employee_id": "e-9", "_id": "m1"}

        # When
        result = json.loads(Responder(redactor)(lambda: payload))

        # Then
        self.assertEqual(
            result,
            {"employee_id": "[REDACTED]", "Internal_Note": "[REDACTED]", "manager_employee_id": "e-9", "_id": "m1"},
        )

    def test_given_a_configured_secret_when_it_appears_anywhere_then_redacts_it(self):
        # Given
        redactor = Redactor(secrets=["graylog-api-token-1", "graylog-api-token-12345", "short"])

        # When
        result = redacted_message("graylog-api-token-12345 rejected; graylog-api-token-1 too; short", redactor)

        # Then
        self.assertEqual(result, "[REDACTED] rejected; [REDACTED] too; short")


class FieldValueRedactionTest(unittest.TestCase):
    def test_given_a_sensitive_reported_field_when_responding_then_masks_each_value_with_a_stable_placeholder(self):
        # Given
        payload = {
            "field": "session_id",
            "top_values": [{"value": "a", "count": 3}, {"value": "b", "count": 1}, {"value": None, "count": 1}],
            "buckets": [{"counts": {"b": 2, "a": 1}}],
        }

        # When
        result = json.loads(respond(lambda: payload, fields=("session_id",)))

        # Then
        self.assertEqual(
            result,
            {
                "field": "session_id",
                "top_values": [
                    {"value": "[REDACTED-1]", "count": 3},
                    {"value": "[REDACTED-2]", "count": 1},
                    {"value": None, "count": 1},
                ],
                "buckets": [{"counts": {"[REDACTED-2]": 2, "[REDACTED-1]": 1}}],
            },
        )

    def test_given_a_sensitive_lookup_table_when_responding_then_masks_the_looked_up_values(self):
        # Given
        payload = {"table": "api-keys", "key": "svc", "single_value": "k", "multi_value": {"v": "k"}, "has_error": False}

        # When
        result = json.loads(respond(lambda: payload, fields=("api-keys",)))

        # Then
        self.assertEqual(result["single_value"], "[REDACTED-1]")
        self.assertEqual(result["multi_value"], "[REDACTED-2]")
        self.assertEqual(result["key"], "svc")

    def test_given_an_ordinary_reported_field_when_responding_then_keeps_the_values(self):
        # Given
        payload = {"top_values": [{"value": "web-1", "count": 3}]}

        # When
        result = json.loads(respond(lambda: payload, fields=("source", "")))

        # Then
        self.assertEqual(result, payload)

    def test_given_a_sensitive_context_field_when_responding_then_redacts_the_context_value_only(self):
        # Given
        payload = {
            "context_field": "session_id",
            "context_value": "abc",
            "anchor": {"session_id": "abc", "value": "kept"},
        }

        # When
        result = json.loads(respond(lambda: payload))

        # Then
        self.assertEqual(
            result,
            {
                "context_field": "session_id",
                "context_value": "[REDACTED]",
                "anchor": {"session_id": "[REDACTED]", "value": "kept"},
            },
        )


if __name__ == "__main__":
    unittest.main()
