"""Docker HEALTHCHECK: exit 0 when the HTTP endpoint answers /healthz.

A server on stdio has no endpoint, so it counts as healthy while it runs.
shortcut: reads the transport from the environment (and .env) only, so a
container started with `--transport streamable-http` as an argument is not
probed; set MCP_TRANSPORT instead when the health check matters.
"""

from __future__ import annotations

import sys
import urllib.request

from .infrastructure.config import Settings
from .interface.mcp_tools import HEALTH_PATH

TIMEOUT_SECONDS = 4


def health_url(settings: Settings) -> str | None:
    """Where this server answers health checks, or None when it serves no HTTP."""
    if settings.transport != "streamable-http":
        return None
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::", "") else settings.host
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{settings.port}{HEALTH_PATH}"


def main() -> int:
    url = health_url(Settings())
    if url is None:
        return 0
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return 0 if response.status == 200 else 1
    except OSError as exc:
        print(f"graylog-mcp health check: {url}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
