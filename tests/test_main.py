import os
import runpy
import sys
import unittest
import warnings
from unittest.mock import patch

import httpx

from graylog_mcp import cli as entry


class FakeServer:
    def __init__(self):
        self.transport = None
        self.options = {}

    def run(self, *, transport, **options):
        self.transport = transport
        self.options = options


class ClientContext:
    def __init__(self):
        self.client = httpx.Client(base_url="https://gl.example")

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc_value, traceback):
        self.client.close()


class CompositionRootTest(unittest.TestCase):
    def test_given_http_client_when_wiring_then_every_service_is_built(self):
        # When
        bundle = entry.wire(httpx.Client(base_url="https://gl.example"))

        # Then
        self.assertIsNotNone(bundle.search)
        self.assertIsNotNone(bundle.analytics)
        self.assertIsNotNone(bundle.alerts)
        self.assertIsNotNone(bundle.views)
        self.assertIsNotNone(bundle.operations)
        self.assertIsNotNone(bundle.configuration)
        self.assertIsNotNone(bundle.capabilities)

    def test_given_no_configuration_when_starting_then_returns_exit_code_one(self):
        # Given
        with patch.dict(os.environ, {}, clear=True):
            # When / Then
            self.assertEqual(entry.main([]), 1)

    def test_given_environment_and_cli_flags_when_parsing_then_flags_win(self):
        # Given
        with patch.dict(
            os.environ,
            {"GRAYLOG_BASE_URL": "https://from-env.example", "GRAYLOG_API_TOKEN": "env-token"},
            clear=True,
        ):
            # When
            settings = entry.parse_args(
                [
                    "--base-url",
                    "https://from-cli.example/",
                    "--api-token",
                    "cli-token",
                    "--no-verify-tls",
                    "--timeout-seconds",
                    "5",
                    "--log-level",
                    "debug",
                ]
            )

        # Then
        self.assertEqual(settings.base_url, "https://from-cli.example")
        self.assertEqual(settings.api_token, "cli-token")
        self.assertFalse(settings.verify_tls)
        self.assertEqual(settings.timeout_seconds, 5.0)
        self.assertEqual(settings.log_level, "DEBUG")

    def test_given_http_transport_flags_when_parsing_then_sets_listener_and_security(self):
        # Given
        with patch.dict(
            os.environ,
            {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
            clear=True,
        ):
            # When
            settings = entry.parse_args(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--streamable-http-path",
                    "/api/mcp/",
                    "--enable-dns-rebinding-protection",
                    "--allowed-hosts",
                    "mcp.example:9000,localhost:9000",
                    "--allowed-origins",
                    "https://app.example",
                ]
            )

        # Then
        self.assertEqual(settings.transport, "streamable-http")
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.streamable_http_path, "/api/mcp")
        self.assertEqual(
            settings.transport_security_settings().model_dump(),
            {
                "enable_dns_rebinding_protection": True,
                "allowed_hosts": ["mcp.example:9000", "localhost:9000"],
                "allowed_origins": ["https://app.example"],
            },
        )

    def test_given_empty_environment_when_flags_supply_required_values_then_accepts_them(self):
        # Given
        with patch.dict(os.environ, {}, clear=True):
            # When
            settings = entry.parse_args(["--base-url", "https://gl.example", "--api-token", "token"])

        # Then
        self.assertEqual(settings.base_url, "https://gl.example")
        self.assertEqual(settings.api_token, "token")

    def test_given_invalid_environment_value_when_parsing_then_raises_config_error_naming_it(self):
        # Given
        with (
            patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://gl.example",
                    "GRAYLOG_API_TOKEN": "token",
                    "GRAYLOG_TIMEOUT_SECONDS": "soon",
                },
                clear=True,
            ),
            # When
            self.assertRaises(entry.ConfigError) as raised,
        ):
            entry.parse_args([])

        # Then
        self.assertRegex(str(raised.exception), "timeout_seconds: .*valid number")

    def test_given_invalid_environment_value_when_flag_replaces_it_then_startup_works(self):
        # Given
        with patch.dict(
            os.environ,
            {
                "GRAYLOG_BASE_URL": "https://gl.example",
                "GRAYLOG_API_TOKEN": "token",
                "GRAYLOG_TIMEOUT_SECONDS": "soon",
                "MCP_PORT": "http",
            },
            clear=True,
        ):
            # When
            settings = entry.parse_args(["--timeout-seconds", "5", "--port", "9000"])

        # Then
        self.assertEqual((settings.timeout_seconds, settings.port), (5.0, 9000))

    def test_given_out_of_range_port_when_parsing_then_rejects_it_at_startup(self):
        # Given
        with (
            patch.dict(
                os.environ,
                {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
                clear=True,
            ),
            # When
            self.assertRaises(entry.ConfigError) as raised,
        ):
            entry.parse_args(["--port", "70000"])

        # Then
        self.assertRegex(str(raised.exception), "port: Input should be less than or equal")

    def test_given_invalid_cli_value_when_parsing_then_raises_config_error(self):
        # Given
        with (
            patch.dict(
                os.environ,
                {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
                clear=True,
            ),
            # When
            self.assertRaises(entry.ConfigError) as raised,
        ):
            entry.parse_args(["--timeout-seconds", "0"])

        # Then
        self.assertRegex(str(raised.exception), "greater than 0")

    def test_given_no_configuration_when_running_module_then_exits_with_one(self):
        # Given
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "argv", ["graylog-mcp"]),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", RuntimeWarning)
            # When
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module("graylog_mcp.cli", run_name="__main__")

        # Then
        self.assertEqual(ctx.exception.code, 1)

    def test_given_stdio_settings_when_running_main_then_stdio_server_starts(self):
        # Given
        server = FakeServer()
        with (
            patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://gl.example",
                    "GRAYLOG_API_TOKEN": "token",
                    "MCP_LOG_LEVEL": "DEBUG",
                },
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server) as build_server,
        ):
            # When / Then
            self.assertEqual(entry.main([]), 0)

        # Then
        self.assertEqual(server.transport, "stdio")
        self.assertEqual(server.options, {})
        self.assertEqual(build_server.call_args.kwargs["log_level"], "DEBUG")
        self.assertEqual(build_server.call_args.kwargs["resource_cache_seconds"], 60.0)

    def test_given_cache_seconds_in_environment_and_flag_when_parsing_then_flag_wins(self):
        # Given
        with patch.dict(
            os.environ,
            {
                "GRAYLOG_BASE_URL": "https://gl.example",
                "GRAYLOG_API_TOKEN": "token",
                "MCP_RESOURCE_CACHE_SECONDS": "120",
            },
            clear=True,
        ):
            # When
            settings = entry.parse_args(["--resource-cache-seconds", "0"])

        # Then
        self.assertEqual(settings.resource_cache_seconds, 0.0)

    def test_given_http_settings_when_running_main_then_streamable_http_server_starts(self):
        # Given
        server = FakeServer()
        with (
            patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://gl.example",
                    "GRAYLOG_API_TOKEN": "token",
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_HOST": "0.0.0.0",
                    "MCP_PORT": "9000",
                    "MCP_STREAMABLE_HTTP_PATH": "/api/mcp",
                    "MCP_ENABLE_DNS_REBINDING_PROTECTION": "true",
                    "MCP_ALLOWED_HOSTS": "mcp.example:9000",
                    "MCP_ALLOWED_ORIGINS": "https://app.example",
                },
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server),
        ):
            # When / Then
            self.assertEqual(entry.main([]), 0)

        # Then
        self.assertEqual(server.transport, "streamable-http")
        self.assertEqual(server.options["host"], "0.0.0.0")
        self.assertEqual(server.options["port"], 9000)
        self.assertEqual(server.options["streamable_http_path"], "/api/mcp")
        self.assertEqual(
            server.options["transport_security"].model_dump(),
            {
                "enable_dns_rebinding_protection": True,
                "allowed_hosts": ["mcp.example:9000"],
                "allowed_origins": ["https://app.example"],
            },
        )


if __name__ == "__main__":
    unittest.main()
