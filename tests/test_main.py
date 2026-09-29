import os
import runpy
import unittest
import warnings
from unittest.mock import patch

import httpx

from graylog_mcp import __main__ as entry


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
            self.assertEqual(entry.main(), 1)

    def test_main_allows_missing_optional_dotenv(self):
        original_import = __import__

        def import_without_dotenv(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError
            return original_import(name, *args, **kwargs)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.__import__", side_effect=import_without_dotenv),
        ):
            self.assertEqual(entry.main(), 1)

    def test_module_entrypoint_exits_with_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module("graylog_mcp.__main__", run_name="__main__")
        self.assertEqual(ctx.exception.code, 1)

    def test_main_runs_stdio_server(self):
        server = FakeServer()
        with (
            patch.dict(
                os.environ,
                {"GRAYLOG_BASE_URL": "https://gl.example", "GRAYLOG_API_TOKEN": "token"},
                clear=True,
            ),
            patch.object(entry, "build_http_client", return_value=ClientContext()),
            patch.object(entry, "build_server", return_value=server),
        ):
            self.assertEqual(entry.main(), 0)
        self.assertEqual(server.transport, "stdio")


if __name__ == "__main__":
    unittest.main()
