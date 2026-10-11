import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from graylog_mcp.infrastructure.config import ConfigError, Settings

BASE = {"GRAYLOG_BASE_URL": "https://gl.example/", "GRAYLOG_API_TOKEN": "tok"}


class SettingsTest(unittest.TestCase):
    def test_given_only_required_values_when_loading_then_defaults_apply(self):
        # Given
        with patch.dict(os.environ, BASE, clear=True):
            # When
            s = Settings().validated()

        # Then
        self.assertEqual(s.base_url, "https://gl.example")
        self.assertTrue(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 30.0)
        self.assertEqual(s.log_level, "INFO")
        self.assertEqual(s.transport, "stdio")
        self.assertEqual(s.host, "127.0.0.1")
        self.assertEqual(s.port, 8000)
        self.assertEqual(s.streamable_http_path, "/mcp")
        self.assertEqual(s.resource_cache_seconds, 60.0)

    def test_given_environment_overrides_when_loading_then_they_apply(self):
        # Given
        with patch.dict(
            os.environ,
            {
                **BASE,
                "GRAYLOG_VERIFY_TLS": "false",
                "GRAYLOG_TIMEOUT_SECONDS": "5",
                "MCP_RESOURCE_CACHE_SECONDS": "0",
            },
            clear=True,
        ):
            # When
            s = Settings().validated()

        # Then
        self.assertFalse(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 5.0)
        self.assertEqual(s.resource_cache_seconds, 0.0)

    def test_given_env_file_and_process_environment_when_loading_then_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            # Given
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "GRAYLOG_BASE_URL=https://from-file.example\nGRAYLOG_API_TOKEN=file-token\nMCP_LOG_LEVEL=WARNING\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://from-environment.example",
                    "GRAYLOG_API_TOKEN": "environment-token",
                },
                clear=True,
            ):
                # When
                settings = Settings(_env_file=env_file).validated()

        # Then
        self.assertEqual(settings.base_url, "https://from-environment.example")
        self.assertEqual(settings.api_token, "environment-token")
        self.assertEqual(settings.log_level, "WARNING")

    def test_given_http_url_when_loading_then_needs_explicit_insecure_opt_in(self):
        # Given
        with (
            patch.dict(
                os.environ,
                {**BASE, "GRAYLOG_BASE_URL": "http://localhost:9000"},
                clear=True,
            ),
            # When
            self.assertRaises(ConfigError) as raised,
        ):
            Settings().validated()

        # Then
        self.assertRegex(str(raised.exception), "GRAYLOG_ALLOW_INSECURE_HTTP")

        # Given
        with patch.dict(
            os.environ,
            {
                **BASE,
                "GRAYLOG_BASE_URL": "http://localhost:9000",
                "GRAYLOG_ALLOW_INSECURE_HTTP": "true",
            },
            clear=True,
        ):
            # When
            settings = Settings().validated()

        # Then
        self.assertEqual(settings.base_url, "http://localhost:9000")

    def test_given_no_values_when_loading_then_reports_every_missing_value(self):
        # Given
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError) as ctx:
            # When
            Settings().validated()

        # Then
        self.assertIn("GRAYLOG_BASE_URL", str(ctx.exception))
        self.assertIn("GRAYLOG_API_TOKEN", str(ctx.exception))

    def test_given_invalid_values_when_loading_then_rejects_each_value(self):
        # Given
        for override in (
            {"GRAYLOG_BASE_URL": "gl.example"},
            {"GRAYLOG_ALLOW_INSECURE_HTTP": "maybe"},
            {"GRAYLOG_VERIFY_TLS": "maybe"},
            {"GRAYLOG_TIMEOUT_SECONDS": "soon"},
            {"GRAYLOG_TIMEOUT_SECONDS": "0"},
            {"MCP_STREAMABLE_HTTP_PATH": "mcp"},
            {"MCP_PORT": "0"},
            {"MCP_PORT": "65536"},
            {"MCP_RESOURCE_CACHE_SECONDS": "-1"},
            {"MCP_RESOURCE_CACHE_SECONDS": "soon"},
        ):
            # When / Then
            with (
                self.subTest(**override),
                patch.dict(os.environ, {**BASE, **override}, clear=True),
                self.assertRaises((ConfigError, ValidationError)),
            ):
                Settings().validated()

    def test_given_comma_separated_hosts_when_building_security_then_splits_comma_separated_values(self):
        # Given
        with patch.dict(
            os.environ,
            {
                **BASE,
                "MCP_ENABLE_DNS_REBINDING_PROTECTION": "true",
                "MCP_ALLOWED_HOSTS": "mcp.example:8000, localhost:8000",
                "MCP_ALLOWED_ORIGINS": "https://one.example, https://two.example",
            },
            clear=True,
        ):
            settings = Settings().validated()

        # When
        security = settings.transport_security_settings().model_dump()

        # Then
        self.assertEqual(
            security,
            {
                "enable_dns_rebinding_protection": True,
                "allowed_hosts": ["mcp.example:8000", "localhost:8000"],
                "allowed_origins": ["https://one.example", "https://two.example"],
            },
        )

    def test_given_boolean_aliases_or_blank_when_loading_then_parses_aliases_or_uses_default(self):
        for value in ("1", "TRUE", "yes", "on"):
            # Given
            with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": value}, clear=True):
                # When / Then
                self.assertTrue(Settings().validated().verify_tls)
        for value in ("0", "FALSE", "no", "off"):
            # Given
            with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": value}, clear=True):
                # When / Then
                self.assertFalse(Settings().validated().verify_tls)
        # Given
        with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": " "}, clear=True):
            # When / Then
            self.assertTrue(Settings().validated().verify_tls)


if __name__ == "__main__":
    unittest.main()
