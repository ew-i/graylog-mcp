import io
import os
import runpy
import sys
import unittest
import warnings
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

import httpx

from graylog_mcp import cli as entry
from graylog_mcp.infrastructure.http_auth import BearerAuthMiddleware


class FakeServer:
    def __init__(self):
        self.transport = None
        self.options = {}

    def run(self, *, transport, **options):
        self.transport = transport
        self.options = options

    def streamable_http_app(self, **options):
        self.transport = "streamable-http"
        self.options = options
        return "mcp-app"


class ClientContext:
    """A Graylog client whose server answers the startup check like Graylog 7.2."""

    def __init__(self, reply=None):
        reply = reply or (lambda request: httpx.Response(200, json={"version": "7.2.0"}))
        self.client = httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(reply))

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
        self.assertEqual(settings.api_token.get_secret_value(), "cli-token")
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
        self.assertEqual(settings.api_token.get_secret_value(), "token")

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

    def test_given_redact_fields_in_environment_and_flag_when_parsing_then_flag_wins(self):
        # Given
        with patch.dict(
            os.environ,
            {
                "GRAYLOG_BASE_URL": "https://gl.example",
                "GRAYLOG_API_TOKEN": "token",
                "MCP_REDACT_FIELDS": "employee_id",
            },
            clear=True,
        ):
            # When
            settings = entry.parse_args(["--redact-fields", " badge_id, internal-note ,"])

        # Then
        self.assertEqual(settings.redact_field_names(), ["badge_id", "internal-note"])

    def test_given_redact_fields_and_token_when_running_main_then_the_server_redacts_both(self):
        # Given
        with (
            patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://gl.example",
                    "GRAYLOG_API_TOKEN": "graylog-token-value",
                    "MCP_REDACT_FIELDS": "employee_id",
                },
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=FakeServer()) as build_server,
        ):
            # When
            entry.main([])

        # Then
        redactor = build_server.call_args.kwargs["redactor"]
        self.assertEqual(
            redactor.redact({"employee_id": "e-1", "message": "sent graylog-token-value"}),
            {"employee_id": "[REDACTED]", "message": "sent [REDACTED]"},
        )

    def run_http_main(self, environment: dict[str, str]) -> tuple[FakeServer, Mock, str]:
        server = FakeServer()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server),
            patch.object(entry.uvicorn, "run") as run,
            redirect_stderr(stderr),
        ):
            # When / Then
            self.assertEqual(entry.main([]), 0)
        return server, run, stderr.getvalue()

    def test_given_http_settings_when_running_main_then_streamable_http_server_starts(self):
        # Given / When
        server, run, stderr = self.run_http_main(
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
            }
        )

        # Then
        self.assertEqual(server.transport, "streamable-http")
        self.assertEqual(server.options["host"], "0.0.0.0")
        self.assertEqual(server.options["streamable_http_path"], "/api/mcp")
        self.assertEqual(
            server.options["transport_security"].model_dump(),
            {
                "enable_dns_rebinding_protection": True,
                "allowed_hosts": ["mcp.example:9000"],
                "allowed_origins": ["https://app.example"],
            },
        )
        run.assert_called_once_with("mcp-app", host="0.0.0.0", port=9000, log_level="info")
        self.assertIn("without authentication", stderr)

    def test_given_an_auth_mode_when_running_main_then_the_app_is_protected(self):
        for mode, extra in (
            ("token", {"MCP_AUTH_TOKEN": "s" * 32, "GRAYLOG_API_TOKEN": "token"}),
            ("graylog", {}),
        ):
            with self.subTest(mode=mode):
                # Given / When
                _, run, stderr = self.run_http_main(
                    {
                        "GRAYLOG_BASE_URL": "https://gl.example",
                        "MCP_TRANSPORT": "streamable-http",
                        "MCP_HOST": "0.0.0.0",
                        "MCP_AUTH_MODE": mode,
                        **extra,
                    }
                )

                # Then
                self.assertIsInstance(run.call_args.args[0], BearerAuthMiddleware)
                self.assertNotIn("warning", stderr)

    def test_given_graylog_mode_when_running_main_then_caches_and_redaction_follow_the_caller(
        self,
    ):
        # Given
        server = FakeServer()
        with (
            patch.dict(
                os.environ,
                {
                    "GRAYLOG_BASE_URL": "https://gl.example",
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_AUTH_MODE": "graylog",
                },
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server) as build_server,
            patch.object(entry.uvicorn, "run"),
        ):
            # When
            entry.main([])

        # Then
        options = build_server.call_args.kwargs
        self.assertIs(options["caller_id"], entry.caller_scope)
        self.assertIs(options["redactor"]._current_secret, entry.caller_token)


class StartupCheckTest(unittest.TestCase):
    """/api/ answers without a login (Graylog 5.1 to 7.2); /api/system needs the token."""

    GRAYLOG = httpx.Response(200, json={"version": "7.2.2", "tagline": "Manage your logs"})

    def graylog(self, hello=None, system=None):
        """A Graylog answering /api/ with `hello` and /api/system with `system`."""
        self.requests = []

        def reply(request):
            self.requests.append((request.url.path, "authorization" in request.headers))
            if request.url.path == "/api/":
                return hello or self.GRAYLOG
            return system or httpx.Response(200, json={"version": "7.2.2"})

        return ClientContext(reply).client

    def check(self, check_token=True, **replies):
        client = self.graylog(**replies)
        client.auth = ("server-token", "token")
        return entry.check_graylog(client, "https://gl.example", check_token)

    def test_given_graylog_answers_when_checking_then_starts_or_explains_why_not(self):
        for replies, starts, says in (
            ({}, True, "connected to Graylog 7.2.2 at https://gl.example"),
            ({"system": httpx.Response(403)}, True, "connected to Graylog 7.2.2"),
            ({"system": httpx.Response(401)}, False, "Graylog 7.2.2 at https://gl.example rejected"),
            ({"hello": httpx.Response(503)}, True, "warning: Graylog at https://gl.example answered"),
            ({"hello": httpx.Response(404)}, False, "check GRAYLOG_BASE_URL"),
            ({"hello": httpx.Response(302)}, False, "HTTP 302"),  # an SSO proxy's login redirect
            ({"hello": httpx.Response(200, text="<html>")}, False, "does not look like"),
            ({"hello": httpx.Response(200, json={"name": "x"})}, False, "does not look like"),
        ):
            with self.subTest(**{k: f"{v.status_code} {v.text[:10]}" for k, v in replies.items()}):
                # When
                ok, message = self.check(**replies)

                # Then
                self.assertIs(ok, starts)
                self.assertIn(says, message)

    def test_given_any_mode_when_checking_then_reads_the_version_without_sending_a_token(self):
        # When
        self.check()

        # Then: only the token check carries the token
        self.assertEqual(self.requests, [("/api/", False), ("/api/system", True)])

    def test_given_no_server_token_when_checking_then_checks_url_and_version_only(self):
        # When
        ok, message = self.check(check_token=False, system=httpx.Response(401))

        # Then
        self.assertTrue(ok)
        self.assertIn("connected to Graylog 7.2.2", message)
        self.assertIn("each client's Graylog token is checked when it connects", message)
        self.assertEqual(self.requests, [("/api/", False)])

    def test_given_graylog_unreachable_when_checking_then_warns_and_starts(self):
        # Given
        def unreachable(request):
            raise httpx.ConnectError("refused", request=request)

        # When
        ok, message = entry.check_graylog(ClientContext(unreachable).client, "https://gl.example", True)

        # Then
        self.assertTrue(ok)
        self.assertIn("cannot reach Graylog", message)

    def start(self, environment, reply):
        """Run main() against a fake Graylog, with the HTTP client main() really builds."""
        stderr = io.StringIO()
        with patch.dict(os.environ, environment, clear=True):
            client = entry.build_http_client(entry.parse_args([]))
        client._transport = httpx.MockTransport(reply)
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(entry, "build_http_client", return_value=client),
            patch.object(entry, "build_server", return_value=FakeServer()) as build_server,
            patch.object(entry.uvicorn, "run"),
            redirect_stderr(stderr),
        ):
            code = entry.main([])
        return code, stderr.getvalue(), build_server

    def test_given_a_rejected_token_when_starting_then_exits_with_one_before_serving(self):
        # When
        code, stderr, build_server = self.start(
            {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "wrong"},
            lambda r: self.GRAYLOG if r.url.path == "/api/" else httpx.Response(401),
        )

        # Then
        self.assertEqual(code, 1)
        self.assertIn("rejected GRAYLOG_API_TOKEN", stderr)
        build_server.assert_not_called()

    def test_given_graylog_mode_when_starting_then_checks_graylog_without_a_token(self):
        # Given no server token: the client's CallerTokenAuth refuses any authenticated call
        requests = []

        def graylog(request):
            requests.append((request.url.path, "authorization" in request.headers))
            return self.GRAYLOG

        # When
        code, stderr, _ = self.start(
            {
                "GRAYLOG_BASE_URL": "https://gl.example",
                "MCP_TRANSPORT": "streamable-http",
                "MCP_AUTH_MODE": "graylog",
            },
            graylog,
        )

        # Then
        self.assertEqual(code, 0)
        self.assertIn("connected to Graylog 7.2.2", stderr)
        self.assertEqual(requests, [("/api/", False)])

    def test_given_graylog_mode_and_a_wrong_url_when_starting_then_exits_with_one(self):
        # When
        code, stderr, _ = self.start(
            {
                "GRAYLOG_BASE_URL": "https://gl.example",
                "MCP_TRANSPORT": "streamable-http",
                "MCP_AUTH_MODE": "graylog",
            },
            lambda request: httpx.Response(404),
        )

        # Then
        self.assertEqual(code, 1)
        self.assertIn("check GRAYLOG_BASE_URL", stderr)


if __name__ == "__main__":
    unittest.main()
