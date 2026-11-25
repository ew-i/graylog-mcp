import io
import os
import runpy
import threading
import time
import unittest
import warnings
from contextlib import redirect_stderr
from unittest.mock import patch

import uvicorn

from graylog_mcp import healthcheck
from graylog_mcp.infrastructure.config import Settings


class HealthUrlTest(unittest.TestCase):
    def test_given_a_transport_and_host_when_building_the_url_then_targets_the_listener(self):
        for options, url in (
            ({"transport": "stdio"}, None),
            ({"transport": "streamable-http"}, "http://127.0.0.1:8000/healthz"),
            ({"transport": "streamable-http", "host": "0.0.0.0", "port": 9000}, "http://127.0.0.1:9000/healthz"),
            ({"transport": "streamable-http", "host": "::"}, "http://127.0.0.1:8000/healthz"),
            ({"transport": "streamable-http", "host": "10.0.0.5"}, "http://10.0.0.5:8000/healthz"),
            ({"transport": "streamable-http", "host": "::1"}, "http://[::1]:8000/healthz"),
        ):
            with self.subTest(**options), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(healthcheck.health_url(Settings(**options)), url)


class HealthCheckTest(unittest.TestCase):
    def run_check(self, environment):
        stderr = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), redirect_stderr(stderr):
            return healthcheck.main(), stderr.getvalue()

    def test_given_stdio_when_checking_then_healthy_without_probing(self):
        with patch.object(healthcheck.urllib.request, "urlopen") as urlopen:
            self.assertEqual(self.run_check({})[0], 0)
        urlopen.assert_not_called()

    def test_given_the_module_run_as_docker_does_when_checking_then_exits_with_its_code(self):
        # When / Then: `python -m graylog_mcp.healthcheck`, as in the Dockerfile
        with (
            patch.dict(os.environ, {}, clear=True),
            warnings.catch_warnings(),
            self.assertRaises(SystemExit) as exit_,
        ):
            warnings.simplefilter("ignore", RuntimeWarning)  # module already imported
            runpy.run_module("graylog_mcp.healthcheck", run_name="__main__")
        self.assertEqual(exit_.exception.code, 0)

    def test_given_nothing_listening_when_checking_then_unhealthy(self):
        # When: a free port, so the connection is refused
        code, stderr = self.run_check({"MCP_TRANSPORT": "streamable-http", "MCP_PORT": "1"})

        # Then
        self.assertEqual(code, 1)
        self.assertIn("http://127.0.0.1:1/healthz", stderr)

    def test_given_a_running_server_when_checking_then_healthy(self):
        # Given a real HTTP server answering /healthz like the MCP server does
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        thread = _serve_in_background(server)
        self.addCleanup(thread.join)
        self.addCleanup(setattr, server, "should_exit", True)
        port = server.servers[0].sockets[0].getsockname()[1]

        # When / Then
        self.assertEqual(self.run_check({"MCP_TRANSPORT": "streamable-http", "MCP_PORT": str(port)})[0], 0)


def _serve_in_background(server):
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - only when the server cannot start
            raise RuntimeError("test server did not start")
        time.sleep(0.01)
    return thread


if __name__ == "__main__":
    unittest.main()
