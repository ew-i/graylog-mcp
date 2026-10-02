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

    def run(self, *, transport):
        self.transport = transport


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
            self.assertRaisesRegex(entry.ConfigError, "valid number"),
        ):
            entry.parse_args([])

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
                    "GRAYLOG_LOG_LEVEL": "DEBUG",
                },
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server) as build_server,
        ):
            self.assertEqual(entry.main([]), 0)
        self.assertEqual(server.transport, "stdio")
        self.assertEqual(build_server.call_args.kwargs["log_level"], "DEBUG")


if __name__ == "__main__":
    unittest.main()
