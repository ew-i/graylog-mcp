"""Client authentication for the Streamable HTTP endpoint.

`token` mode compares the client's bearer token with the configured shared
secret. `graylog` mode takes the client's bearer token to be its own Graylog
API token: Graylog checks it, and the server then makes that client's Graylog
calls with it, so Graylog applies the client's own permissions.

The middleware is plain ASGI so streamed (SSE) responses pass through
untouched. The caller's token travels in a context variable: the MCP SDK hands
each message's context to the tool that handles it, and worker threads inherit it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextvars import ContextVar
from typing import Any

import httpx

from ..domain.errors import AccessDeniedError, BackendUnavailableError
from .config import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]
Verifier = Callable[[str], Awaitable[bool]]

# Graylog asks every authenticated user for this; only an unknown token gets 401.
VERIFY_PATH = "/api/system"
# A verified token is trusted this long without asking Graylog again. Graylog
# still checks it on every call the server makes with it.
VERIFIED_SECONDS = 60.0

_caller_token: ContextVar[str | None] = ContextVar("graylog_caller_token", default=None)


def caller_token() -> str | None:
    """The Graylog token of the client whose request is being handled, if any."""
    return _caller_token.get()


def caller_scope() -> str:
    """A stable, non-secret name for the current caller, for per-caller caches."""
    token = _caller_token.get()
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


class CallerTokenAuth(httpx.Auth):
    """Authenticates each Graylog request as the client being served."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = _caller_token.get()
        if not token:
            raise AccessDeniedError("no client Graylog token for this request")
        yield from httpx.BasicAuth(token, "token").auth_flow(request)


class StaticTokenVerifier:
    def __init__(self, expected: str) -> None:
        self._expected = expected.encode()

    async def __call__(self, token: str) -> bool:
        return hmac.compare_digest(token.encode(), self._expected)


class GraylogTokenVerifier:
    """Asks Graylog whether a token is valid, and remembers valid ones briefly."""

    def __init__(
        self,
        client: httpx.Client,
        ttl: float = VERIFIED_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = client
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._verified: dict[str, float] = {}

    async def __call__(self, token: str) -> bool:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            verified_at = self._verified.get(key)
        if verified_at is not None and now - verified_at < self._ttl:
            return True
        if not await asyncio.to_thread(self._ask_graylog, token):
            return False
        with self._lock:
            self._verified = {k: t for k, t in self._verified.items() if now - t < self._ttl}
            self._verified[key] = now
        return True

    def _ask_graylog(self, token: str) -> bool:
        try:
            response = self._http.get(VERIFY_PATH, auth=(token, "token"))
        except httpx.RequestError as exc:
            raise BackendUnavailableError(f"could not reach Graylog: {exc}") from exc
        if response.status_code >= 500:
            raise BackendUnavailableError(f"Graylog returned HTTP {response.status_code}")
        # 403 means a valid token without permission for this path. Anything else
        # (404 from a wrong base URL, 302 from an SSO proxy) proves nothing.
        return response.status_code in (200, 403)


class BearerAuthMiddleware:
    """Refuses HTTP requests without a bearer token the verifier accepts."""

    def __init__(self, app: App, verify: Verifier, forward_token: bool = False) -> None:
        self._app = app
        self._verify = verify
        self._forward_token = forward_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        token = _bearer_token(scope)
        if token is None:
            await _refuse(send, 401, "send an Authorization: Bearer header")
            return
        try:
            accepted = await self._verify(token)
        except BackendUnavailableError as exc:
            await _refuse(send, 503, f"cannot check the token: {exc}")
            return
        if not accepted:
            await _refuse(send, 401, "the bearer token is not valid")
            return
        if not self._forward_token:
            await self._app(scope, receive, send)
            return
        reset = _caller_token.set(token)
        try:
            await self._app(scope, receive, send)
        finally:
            _caller_token.reset(reset)


def protect(app: App, settings: Settings, client: httpx.Client) -> App:
    """Wrap `app` in the authentication `settings.auth_mode` asks for."""
    if settings.auth_mode == "token":
        return BearerAuthMiddleware(
            app, StaticTokenVerifier(settings.auth_token.get_secret_value())
        )
    if settings.auth_mode == "graylog":
        return BearerAuthMiddleware(app, GraylogTokenVerifier(client), forward_token=True)
    return app


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if scheme.lower() == "bearer" and token else None
    return None


async def _refuse(send: Send, status: int, reason: str) -> None:
    body = json.dumps({"error": reason}).encode()
    headers = [(b"content-type", b"application/json")]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="graylog-mcp"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
