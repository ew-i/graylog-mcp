"""Thin JSON-over-HTTP client for the Graylog REST API, shared by all adapters.

It owns the transport concerns every adapter needs: auth, required headers,
error mapping to domain errors, and JSON decoding. Adapters own the
endpoint-specific request and response shapes.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from ..domain.errors import AccessDeniedError, BackendError, BackendUnavailableError, NotFoundError
from ..domain.models import Between, LastSeconds, TimeWindow
from .config import Settings
from .http_auth import CallerTokenAuth


def build_http_client(settings: Settings) -> httpx.Client:
    """Graylog tokens are sent as basic-auth username with password 'token'.

    In graylog auth mode there is no server token: each request carries the
    token of the client being served.
    """
    auth: httpx.Auth | tuple[str, str] = (
        CallerTokenAuth()
        if settings.auth_mode == "graylog"
        else (settings.api_token.get_secret_value(), "token")
    )
    return httpx.Client(
        base_url=settings.base_url,
        auth=auth,
        headers={"Accept": "application/json", "X-Requested-By": "graylog-mcp"},
        timeout=settings.timeout_seconds,
        verify=settings.verify_tls,
    )


def segment(value: str) -> str:
    """Escape one URL path segment (also escapes '/')."""
    return quote(value, safe="")


class GraylogApi:
    def __init__(self, client: httpx.Client) -> None:
        self._http = client

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._send("GET", path, params=params)

    def get_object(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return _expect_object(path, self.get(path, params))

    def get_list(self, path: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        body = self.get(path, params)
        if not isinstance(body, list):
            raise BackendError(f"expected a JSON array from {path}")
        return body

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        return self._send("POST", path, json=body)

    def post_object(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return _expect_object(path, self._send("POST", path, json=body))

    def post_list(self, path: str, body: Mapping[str, Any]) -> list[Any]:
        result = self._send("POST", path, json=body)
        if not isinstance(result, list):
            raise BackendError(f"expected a JSON array from {path}")
        return result

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        status = response.status_code
        if status in (401, 403):
            raise AccessDeniedError(
                f"Graylog refused access to {path} (HTTP {status}); "
                "check the token's permissions and that a permitted stream_id was used"
            )
        if status == 404:
            raise NotFoundError(f"Graylog has no resource at {path}")
        if status >= 400:
            raise BackendError(f"Graylog returned HTTP {status} for {path}: {response.text[:300]}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BackendError(f"Graylog sent a non-JSON response for {path}") from exc

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send, retrying failures a restarting or overloaded Graylog causes.

        Every call only reads, so a retry cannot change anything. Timeouts are
        not retried: each already took the full timeout.
        """
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                raise BackendUnavailableError(f"Graylog did not answer in time ({path})") from exc
            except _RETRYABLE_ERRORS as exc:
                if last:
                    raise BackendUnavailableError(f"could not reach Graylog: {exc}") from exc
                time.sleep(_backoff(attempt))
                continue
            except httpx.RequestError as exc:
                raise BackendUnavailableError(f"could not reach Graylog: {exc}") from exc
            if response.status_code not in _RETRYABLE_STATUS or last:
                return response
            time.sleep(_retry_after(response) or _backoff(attempt))
        raise AssertionError("unreachable")  # pragma: no cover


# Answers a restarting Graylog or a proxy in front of it gives; worth asking again.
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
# Failures before Graylog got the request, or a connection it dropped while restarting.
_RETRYABLE_ERRORS = (httpx.ConnectError, httpx.RemoteProtocolError)
RETRIES = 2
MAX_RETRY_WAIT_SECONDS = 5.0


def _backoff(attempt: int) -> float:
    """0.5 s, then 1 s, each with up to 50% jitter so clients don't retry in step."""
    return 0.5 * 2**attempt * (1 + random.random() / 2)


def _retry_after(response: httpx.Response) -> float | None:
    """Graylog's or a proxy's Retry-After in seconds, capped so a tool call can't hang."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:  # absent, or an HTTP date: fall back to the backoff
        return None
    return min(max(seconds, 0.0), MAX_RETRY_WAIT_SECONDS)


def _expect_object(path: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise BackendError(f"unexpected response shape from {path}")
    return body


# -- parsing helpers shared by adapters ---------------------------------------


def timerange_json(window: TimeWindow) -> dict[str, Any]:
    if isinstance(window, LastSeconds):
        return {"type": "relative", "range": window.seconds}
    if isinstance(window, Between):
        return {"type": "absolute", "from": window.start, "to": window.end}
    raise TypeError(window)


def text(value: Any) -> str | None:
    return None if value is None else str(value)


def integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def total_count(body: Mapping[str, Any], *keys: str) -> int | None:
    """`pagination.total` counts the filtered matches; some endpoints' top-level
    `total` counts everything (e.g. /api/streams/paginated), so it comes first."""
    pagination = body.get("pagination")
    if isinstance(pagination, dict):
        value = integer(pagination.get("total"))
        if value is not None:
            return value
    for key in keys:
        value = integer(body.get(key))
        if value is not None:
            return value
    return None


def page_number(offset: int, limit: int) -> int:
    """1-based page for endpoints that page by number instead of offset.

    Their next page always starts a whole page later (`offset + limit`), even
    when an adapter dropped malformed entries from this one.
    """
    return offset // limit + 1


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def objects(value: Any) -> list[dict[str, Any]]:
    """Keep only the dict items of a list (anything else -> [])."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def first_list(body: Mapping[str, Any], keys: Iterable[str]) -> list[dict[str, Any]]:
    """Paginated Graylog responses name their item list differently across versions."""
    for key in keys:
        if isinstance(body.get(key), list):
            return objects(body[key])
    return []


def short_class(name: Any) -> str | None:
    return None if not name else str(name).rsplit(".", 1)[-1]
