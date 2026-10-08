import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from graylog_mcp.infrastructure.config import ConfigError, Settings

BASE = {"GRAYLOG_BASE_URL": "https://gl.example/", "GRAYLOG_API_TOKEN": "tok"}


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, BASE, clear=True):
            s = Settings().validated()
        self.assertEqual(s.base_url, "https://gl.example")
        self.assertTrue(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 30.0)
        self.assertEqual(s.log_level, "INFO")
        self.assertEqual(s.transport, "stdio")
        self.assertEqual(s.host, "127.0.0.1")
        self.assertEqual(s.port, 8000)
        self.assertEqual(s.streamable_http_path, "/mcp")

    def test_overrides(self):
        with patch.dict(
            os.environ,
            {**BASE, "GRAYLOG_VERIFY_TLS": "false", "GRAYLOG_TIMEOUT_SECONDS": "5"},
            clear=True,
        ):
            s = Settings().validated()
        self.assertFalse(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 5.0)

    def test_process_environment_overrides_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "GRAYLOG_BASE_URL=https://from-file.example\n"
                "GRAYLOG_API_TOKEN=file-token\n"
                "MCP_LOG_LEVEL=WARNING\n",
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
                settings = Settings(_env_file=env_file).validated()

        self.assertEqual(settings.base_url, "https://from-environment.example")
        self.assertEqual(settings.api_token, "environment-token")
        self.assertEqual(settings.log_level, "WARNING")

    def test_http_requires_explicit_development_override(self):
        with (
            patch.dict(
                os.environ,
                {**BASE, "GRAYLOG_BASE_URL": "http://localhost:9000"},
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "GRAYLOG_ALLOW_INSECURE_HTTP"),
        ):
            Settings().validated()

        with patch.dict(
            os.environ,
            {
                **BASE,
                "GRAYLOG_BASE_URL": "http://localhost:9000",
                "GRAYLOG_ALLOW_INSECURE_HTTP": "true",
            },
            clear=True,
        ):
            settings = Settings().validated()
        self.assertEqual(settings.base_url, "http://localhost:9000")

    def test_missing_values_are_all_reported(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError) as ctx:
            Settings().validated()
        self.assertIn("GRAYLOG_BASE_URL", str(ctx.exception))
        self.assertIn("GRAYLOG_API_TOKEN", str(ctx.exception))

    def test_invalid_values(self):
        for override in (
            {"GRAYLOG_BASE_URL": "gl.example"},
            {"GRAYLOG_ALLOW_INSECURE_HTTP": "maybe"},
            {"GRAYLOG_VERIFY_TLS": "maybe"},
            {"GRAYLOG_TIMEOUT_SECONDS": "soon"},
            {"GRAYLOG_TIMEOUT_SECONDS": "0"},
            {"MCP_STREAMABLE_HTTP_PATH": "mcp"},
            {"MCP_PORT": "0"},
            {"MCP_PORT": "65536"},
        ):
            with (
                self.subTest(**override),
                patch.dict(os.environ, {**BASE, **override}, clear=True),
                self.assertRaises((ConfigError, ValidationError)),
            ):
                Settings().validated()

    def test_transport_security_settings_split_csv_values(self):
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

        self.assertEqual(
            settings.transport_security_settings().model_dump(),
            {
                "enable_dns_rebinding_protection": True,
                "allowed_hosts": ["mcp.example:8000", "localhost:8000"],
                "allowed_origins": ["https://one.example", "https://two.example"],
            },
        )

    def test_boolean_aliases_and_blank_values(self):
        for value in ("1", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": value}, clear=True):
                self.assertTrue(Settings().validated().verify_tls)
        for value in ("0", "FALSE", "no", "off"):
            with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": value}, clear=True):
                self.assertFalse(Settings().validated().verify_tls)
        with patch.dict(os.environ, {**BASE, "GRAYLOG_VERIFY_TLS": " "}, clear=True):
            self.assertTrue(Settings().validated().verify_tls)


if __name__ == "__main__":
    unittest.main()
