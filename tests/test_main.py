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
    def test_wire_builds_all_services(self):
        bundle = entry.wire(httpx.Client(base_url="https://gl.example"))
        self.assertIsNotNone(bundle.search)
        self.assertIsNotNone(bundle.analytics)
        self.assertIsNotNone(bundle.alerts)
        self.assertIsNotNone(bundle.views)
        self.assertIsNotNone(bundle.operations)
        self.assertIsNotNone(bundle.configuration)

    def test_main_rejects_missing_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(entry.main([]), 1)

    def test_cli_arguments_override_environment_defaults(self):
        with patch.dict(
            os.environ,
            {"GRAYLOG_BASE_URL": "https://from-env.example", "GRAYLOG_API_TOKEN": "env-token"},
            clear=True,
        ):
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
        self.assertEqual(settings.base_url, "https://from-cli.example")
        self.assertEqual(settings.api_token, "cli-token")
        self.assertFalse(settings.verify_tls)
        self.assertEqual(settings.timeout_seconds, 5.0)
        self.assertEqual(settings.log_level, "DEBUG")

    def test_cli_arguments_configure_streamable_http_security(self):
        with patch.dict(
            os.environ,
            {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
            clear=True,
        ):
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

    def test_cli_arguments_can_supply_required_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = entry.parse_args(
                ["--base-url", "https://gl.example", "--api-token", "token"]
            )
        self.assertEqual(settings.base_url, "https://gl.example")
        self.assertEqual(settings.api_token, "token")

    def test_invalid_environment_setting_is_reported_as_config_error(self):
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
            self.assertRaisesRegex(entry.ConfigError, "timeout_seconds: .*valid number"),
        ):
            entry.parse_args([])

    def test_cli_flag_replaces_an_invalid_environment_value(self):
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
            settings = entry.parse_args(["--timeout-seconds", "5", "--port", "9000"])
        self.assertEqual((settings.timeout_seconds, settings.port), (5.0, 9000))

    def test_out_of_range_port_is_rejected_at_startup(self):
        with (
            patch.dict(
                os.environ,
                {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
                clear=True,
            ),
            self.assertRaisesRegex(entry.ConfigError, "port: Input should be less than or equal"),
        ):
            entry.parse_args(["--port", "70000"])

    def test_invalid_cli_setting_is_reported_as_config_error(self):
        with (
            patch.dict(
                os.environ,
                {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
                clear=True,
            ),
            self.assertRaisesRegex(entry.ConfigError, "greater than 0"),
        ):
            entry.parse_args(["--timeout-seconds", "0"])

    def test_cli_module_exits_with_configuration_error(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "argv", ["graylog-mcp"]),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", RuntimeWarning)
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module("graylog_mcp.cli", run_name="__main__")
        self.assertEqual(ctx.exception.code, 1)

    def test_main_runs_stdio_server(self):
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
            self.assertEqual(entry.main([]), 0)
        self.assertEqual(server.transport, "stdio")
        self.assertEqual(server.options, {})
        self.assertEqual(build_server.call_args.kwargs["log_level"], "DEBUG")

    def test_main_runs_streamable_http_server(self):
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
            self.assertEqual(entry.main([]), 0)

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
