import asyncio
import base64
import json
import unittest

import httpx
from starlette.testclient import TestClient

from graylog_mcp import __version__
from graylog_mcp.cli import wire
from graylog_mcp.domain.errors import AccessDeniedError, BackendUnavailableError
from graylog_mcp.infrastructure.config import Settings
from graylog_mcp.infrastructure.http_auth import (
    BearerAuthMiddleware,
    CallerTokenAuth,
    GraylogTokenVerifier,
    StaticTokenVerifier,
    caller_scope,
    caller_token,
    protect,
)
from graylog_mcp.interface.mcp_tools import HEALTH_PATH, build_server
from graylog_mcp.interface.redaction import Redactor

SHARED_SECRET = "s" * 40
ALICE = "alice-graylog-token"
BOB = "bob-graylog-token"
ACCEPT = {"Accept": "application/json, text/event-stream"}


def basic_user(request: httpx.Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    return base64.b64decode(header[6:]).decode().partition(":")[0]


class FakeGraylog:
    """Answers /api/system for known tokens and records who asked what."""

    def __init__(self, tokens=(ALICE, BOB)):
        self.tokens = set(tokens)
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = basic_user(request)
        self.calls.append((request.url.path, user))
        if user not in self.tokens:
            return httpx.Response(401)
        if request.url.path == "/api/system":
            # Graylog echoing the caller's token: redaction must remove it.
            return httpx.Response(200, json={"version": "7.2.0", "hostname": f"seen {user}"})
        if request.url.path == "/api/streams/paginated":
            name = user.partition("-")[0]
            streams = [{"id": f"{name}-stream", "title": name}]
            return httpx.Response(200, json={"elements": streams, "pagination": {"total": 1}})
        return httpx.Response(404)


def http_app(mode: str, graylog: FakeGraylog):
    settings = Settings(
        base_url="https://gl.example",
        api_token="server-token" if mode != "graylog" else "",
        transport="streamable-http",
        auth_mode=mode,
        auth_token=SHARED_SECRET,
    ).validated()
    auth = CallerTokenAuth() if mode == "graylog" else ("server-token", "token")
    client = httpx.Client(base_url=settings.base_url, transport=httpx.MockTransport(graylog), auth=auth)
    per_caller = mode == "graylog"
    server = build_server(
        wire(client),
        redactor=Redactor(current_secret=caller_token if per_caller else lambda: None),
        caller_id=caller_scope if per_caller else lambda: "",
    )
    app = server.streamable_http_app(transport_security=settings.transport_security_settings())
    return protect(app, settings, client, public_paths=frozenset({HEALTH_PATH}))


class McpClient:
    """Just enough of an MCP client to call tools and read resources over HTTP."""

    def __init__(self, http: TestClient, token: str | None):
        self.http = http
        self.headers = dict(ACCEPT)
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"
        self.next_id = 1

    def post(self, message: dict) -> httpx.Response:
        return self.http.post("/mcp", json=message, headers=self.headers)

    def initialize(self) -> httpx.Response:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        if response.status_code == 200:
            self.headers["mcp-session-id"] = response.headers["mcp-session-id"]
            self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def request(self, method: str, params: dict) -> httpx.Response:
        self.next_id += 1
        return self.post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})

    def result(self, method: str, params: dict) -> dict:
        response = self.request(method, params)
        data = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        return json.loads(data[-1])["result"]

    def tool(self, name: str) -> dict:
        return json.loads(self.result("tools/call", {"name": name, "arguments": {}})["content"][0]["text"])

    def resource(self, uri: str) -> dict:
        return json.loads(self.result("resources/read", {"uri": uri})["contents"][0]["text"])


class TokenModeTest(unittest.TestCase):
    def test_given_token_mode_when_clients_connect_then_only_the_shared_secret_is_served(self):
        graylog = FakeGraylog(tokens=("server-token",))
        with TestClient(http_app("token", graylog), base_url="http://127.0.0.1:8000") as http:
            for token, status in (
                (None, 401),
                ("wrong", 401),
                (SHARED_SECRET + "x", 401),
                (SHARED_SECRET, 200),
            ):
                with self.subTest(token=token):
                    # When
                    response = McpClient(http, token).initialize()

                    # Then
                    self.assertEqual(response.status_code, status)
                    if status == 401:
                        self.assertIn("Bearer", response.headers["www-authenticate"])

            # Given an accepted client
            client = McpClient(http, SHARED_SECRET)
            client.initialize()

            # When
            status = client.tool("cluster_status")

        # Then Graylog was asked with the server's own token
        self.assertEqual(status["version"], "7.2.0")
        self.assertEqual({user for _, user in graylog.calls}, {"server-token"})

    def test_given_a_non_bearer_authorization_when_connecting_then_it_is_refused(self):
        with TestClient(http_app("token", FakeGraylog()), base_url="http://127.0.0.1:8000") as http:
            for header in (f"Basic {SHARED_SECRET}", "Bearer", "Bearer "):
                with self.subTest(header=header):
                    # When
                    response = http.post("/mcp", json={}, headers={**ACCEPT, "Authorization": header})

                    # Then
                    self.assertEqual(response.status_code, 401)


class GraylogModeTest(unittest.TestCase):
    def test_given_graylog_mode_when_two_clients_call_tools_then_each_uses_its_own_token(self):
        # Given
        graylog = FakeGraylog()
        with TestClient(http_app("graylog", graylog), base_url="http://127.0.0.1:8000") as http:
            alice, bob = McpClient(http, ALICE), McpClient(http, BOB)
            alice.initialize()
            bob.initialize()
            graylog.calls.clear()

            # When
            alice_status = alice.tool("cluster_status")
            bob_status = bob.tool("cluster_status")

        # Then each call reached Graylog as its own client, and no token leaked back
        self.assertEqual(graylog.calls, [("/api/system", ALICE), ("/api/system", BOB)])
        self.assertEqual(alice_status["hostname"], "seen [REDACTED]")
        self.assertEqual(bob_status["hostname"], "seen [REDACTED]")

    def test_given_graylog_mode_when_a_client_uses_another_clients_session_then_it_reads_as_itself(
        self,
    ):
        # Given Bob knows Alice's session ID
        graylog = FakeGraylog()
        with TestClient(http_app("graylog", graylog), base_url="http://127.0.0.1:8000") as http:
            alice = McpClient(http, ALICE)
            alice.initialize()
            bob = McpClient(http, BOB)
            bob.headers["mcp-session-id"] = alice.headers["mcp-session-id"]
            bob.tool("cluster_status")  # verifies Bob's token once
            graylog.calls.clear()

            # When both call a tool on the same session
            bob.tool("cluster_status")
            alice.tool("cluster_status")

        # Then each message reaches Graylog with its sender's token, not the session opener's
        self.assertEqual(graylog.calls, [("/api/system", BOB), ("/api/system", ALICE)])

    def test_given_graylog_mode_when_clients_read_a_resource_then_the_cache_is_per_client(self):
        # Given
        graylog = FakeGraylog()
        with TestClient(http_app("graylog", graylog), base_url="http://127.0.0.1:8000") as http:
            alice, bob = McpClient(http, ALICE), McpClient(http, BOB)
            alice.initialize()
            bob.initialize()

            # When
            first = alice.resource("graylog://streams")
            again = alice.resource("graylog://streams")
            other = bob.resource("graylog://streams")

        # Then
        self.assertEqual([s["id"] for s in first["streams"]], ["alice-stream"])
        self.assertEqual(again, first)
        self.assertEqual([s["id"] for s in other["streams"]], ["bob-stream"])
        stream_reads = [user for path, user in graylog.calls if path == "/api/streams/paginated"]
        self.assertEqual(stream_reads, [ALICE, BOB])

    def test_given_graylog_mode_when_a_token_is_unknown_then_the_client_is_refused(self):
        # Given
        graylog = FakeGraylog()
        with TestClient(http_app("graylog", graylog), base_url="http://127.0.0.1:8000") as http:
            # When
            response = McpClient(http, "unknown-token").initialize()

        # Then
        self.assertEqual(response.status_code, 401)
        self.assertEqual(graylog.calls, [("/api/system", "unknown-token")])


class HealthTest(unittest.TestCase):
    def test_given_any_auth_mode_when_checking_health_then_answers_without_a_token(self):
        for mode in ("none", "token", "graylog"):
            with (
                self.subTest(mode=mode),
                TestClient(http_app(mode, FakeGraylog()), base_url="http://127.0.0.1:8000") as http,
            ):
                # When
                health = http.get("/healthz")
                mcp = http.post("/mcp", json={}, headers=ACCEPT)

                # Then the health check is open, the MCP endpoint is not (outside none)
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok", "version": __version__})
                if mode != "none":
                    self.assertEqual(mcp.status_code, 401)


class AuditLogTest(unittest.TestCase):
    def test_given_tool_calls_when_served_then_each_is_logged_with_caller_arguments_and_outcome(
        self,
    ):
        # Given two clients in graylog mode
        graylog = FakeGraylog()
        with TestClient(http_app("graylog", graylog), base_url="http://127.0.0.1:8000") as http:
            alice, bob = McpClient(http, ALICE), McpClient(http, BOB)
            alice.initialize()
            bob.initialize()

            # When
            with self.assertLogs("graylog_mcp.audit", level="INFO") as logs:
                alice.tool("cluster_status")
                bob.result(
                    "tools/call",
                    {
                        "name": "stream_rules",
                        "arguments": {"stream_id": f"password={'p' * 20} " + "x" * 2000},
                    },
                )
                bob.result("tools/call", {"name": "find_recent_logs", "arguments": {"query": 1}})

        # Then
        entries = [json.loads(line.partition("tool_call ")[2]) for line in logs.output]
        ok, failed, invalid = entries
        self.assertEqual((ok["tool"], ok["outcome"], ok["kind"]), ("cluster_status", "ok", None))
        self.assertEqual((failed["outcome"], failed["kind"]), ("error", "NotFound"))
        self.assertEqual((invalid["outcome"], invalid["kind"]), ("error", None))
        self.assertTrue(all(isinstance(entry["ms"], int) for entry in entries))
        # who: a stable, non-secret name per client
        self.assertEqual(len(ok["caller"]), 12)
        self.assertNotEqual(ok["caller"], failed["caller"])
        self.assertEqual(failed["caller"], invalid["caller"])
        self.assertNotIn(ALICE, "".join(logs.output))
        # what: redacted and shortened
        stream_id = failed["arguments"]["stream_id"]
        self.assertNotIn("p" * 20, stream_id)
        self.assertTrue(stream_id.endswith("chars]"))

    def test_given_no_per_client_tokens_when_logging_then_the_caller_is_unknown(self):
        with TestClient(http_app("token", FakeGraylog()), base_url="http://127.0.0.1:8000") as http:
            client = McpClient(http, SHARED_SECRET)
            client.initialize()
            with self.assertLogs("graylog_mcp.audit", level="INFO") as logs:
                client.tool("cluster_status")

        self.assertIsNone(json.loads(logs.output[0].partition("tool_call ")[2])["caller"])


class GraylogTokenVerifierTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0

    def verifier(self, handler) -> GraylogTokenVerifier:
        client = httpx.Client(base_url="https://gl.example", transport=httpx.MockTransport(handler))
        return GraylogTokenVerifier(client, ttl=60, clock=lambda: self.now)

    def test_given_graylog_answers_when_verifying_then_only_200_and_403_mean_valid(self):
        for status, valid in (
            (200, True),
            (403, True),
            (401, False),
            (404, False),  # wrong base URL
            (302, False),  # SSO proxy redirecting to its login page
        ):
            with self.subTest(status=status):
                # Given
                verify = self.verifier(lambda request, status=status: httpx.Response(status))

                # When / Then
                self.assertIs(asyncio.run(verify("token")), valid)

    def test_given_graylog_fails_when_verifying_then_reports_it_unavailable(self):
        def unreachable(request):
            raise httpx.ConnectError("refused")

        for handler in (lambda request: httpx.Response(502), unreachable):
            with self.subTest(handler=handler), self.assertRaises(BackendUnavailableError):
                # When / Then
                asyncio.run(self.verifier(handler)("token"))

    def test_given_a_verified_token_when_verifying_again_then_asks_graylog_once_per_ttl(self):
        # Given
        calls = []
        verify = self.verifier(lambda request: calls.append(request) or httpx.Response(200))

        # When
        asyncio.run(verify("token"))
        self.now = 59
        asyncio.run(verify("token"))
        self.now = 61
        asyncio.run(verify("token"))

        # Then
        self.assertEqual(len(calls), 2)

    def test_given_an_invalid_token_when_verifying_again_then_asks_graylog_again(self):
        # Given
        calls = []
        verify = self.verifier(lambda request: calls.append(request) or httpx.Response(401))

        # When
        asyncio.run(verify("token"))
        asyncio.run(verify("token"))

        # Then
        self.assertEqual(len(calls), 2)


class MiddlewareTest(unittest.TestCase):
    def test_given_graylog_unreachable_when_verifying_then_answers_503(self):
        # Given
        async def unavailable(token):
            raise BackendUnavailableError("down")

        async def app(scope, receive, send):  # pragma: no cover - never reached
            raise AssertionError("the request must not reach the app")

        http = TestClient(BearerAuthMiddleware(app, unavailable))

        # When
        response = http.get("/mcp", headers={"Authorization": "Bearer token"})

        # Then
        self.assertEqual(response.status_code, 503)
        self.assertIn("down", response.json()["error"])

    def test_given_static_verifier_when_comparing_then_accepts_only_the_exact_secret(self):
        verify = StaticTokenVerifier(SHARED_SECRET)
        self.assertTrue(asyncio.run(verify(SHARED_SECRET)))
        self.assertFalse(asyncio.run(verify(SHARED_SECRET[:-1])))

    def test_given_no_auth_mode_when_protecting_then_the_app_is_unchanged(self):
        settings = Settings(base_url="https://gl.example", api_token="t").validated()
        app = object()
        self.assertIs(protect(app, settings, httpx.Client()), app)


class CallerTokenTest(unittest.TestCase):
    def test_given_no_caller_when_calling_graylog_then_refuses_to_send_the_request(self):
        # Given
        client = httpx.Client(
            base_url="https://gl.example",
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            auth=CallerTokenAuth(),
        )

        # When / Then
        with self.assertRaises(AccessDeniedError):
            client.get("/api/system")
        self.assertIsNone(caller_token())
        self.assertEqual(caller_scope(), "")


if __name__ == "__main__":
    unittest.main()
