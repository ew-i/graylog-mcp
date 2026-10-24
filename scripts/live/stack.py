"""Start or stop a throwaway Graylog stack for scripts/live_check.py.

usage: python scripts/live/stack.py up|down VERSION

`up` starts Graylog VERSION with MongoDB and OpenSearch, waits until the API
answers, and creates a global GELF HTTP input. The stack listens on localhost
only, with the test login admin / admin. `down` removes it with its volumes.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

COMPOSE = Path(__file__).with_name("compose.yml")
STACKS = {
    "5.1": {"MONGO_VERSION": "6.0", "OPENSEARCH_VERSION": "2.4.1", "port": 29000},
    "6.3": {"MONGO_VERSION": "7.0", "OPENSEARCH_VERSION": "2.15.0", "port": 39000},
    "7.2": {"MONGO_VERSION": "7.0", "OPENSEARCH_VERSION": "2.19.6", "port": 49000},
}
AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()
GELF_INPUT = "org.graylog2.inputs.gelf.http.GELFHttpInput"


def settings(version: str) -> dict[str, str]:
    stack = STACKS[version]
    return {
        "GRAYLOG_VERSION": version,
        "GRAYLOG_SLUG": version.replace(".", "-"),
        "MONGO_VERSION": stack["MONGO_VERSION"],
        "OPENSEARCH_VERSION": stack["OPENSEARCH_VERSION"],
        "WEB_PORT": str(stack["port"]),
        "GELF_PORT": str(stack["port"] + 1),
    }


def api(base: str, method: str, path: str, body: dict | None = None) -> object:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": AUTH,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-By": "live-check",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def wait_ready(base: str, timeout: float = 300) -> str:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return api(base, "GET", "/api/system")["version"]
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
            if time.monotonic() > deadline:
                raise SystemExit(f"Graylog at {base} did not become ready") from None
            time.sleep(5)


def ensure_gelf_input(base: str) -> None:
    inputs = api(base, "GET", "/api/system/inputs")["inputs"]
    if any(i["type"] == GELF_INPUT for i in inputs):
        return
    api(
        base,
        "POST",
        "/api/system/inputs",
        {
            "title": "GELF HTTP (live check)",
            "type": GELF_INPUT,
            "global": True,
            "configuration": {
                "bind_address": "0.0.0.0",
                "port": 12201,
                "recv_buffer_size": 1048576,
                "decompress_size_limit": 8388608,
            },
        },
    )


def main(action: str, version: str) -> None:
    if version not in STACKS:
        raise SystemExit(f"unknown version {version}; choose from {', '.join(STACKS)}")
    env = {**os.environ, **settings(version)}
    compose = ["docker", "compose", "-f", str(COMPOSE)]
    if action == "down":
        subprocess.run([*compose, "down", "--volumes"], env=env, check=True)
        return
    # No --wait: the image's healthcheck gives up after about 2 minutes, before a first
    # start (5.1) finishes, and --wait then fails; wait_ready polls the API instead.
    subprocess.run([*compose, "up", "--detach"], env=env, check=True)
    base = f"http://localhost:{env['WEB_PORT']}"
    running = wait_ready(base)
    ensure_gelf_input(base)
    print(
        f"Graylog {running} ready: --base-url {base} --user admin --password admin "
        f"--gelf-url http://localhost:{env['GELF_PORT']}/gelf"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("up", "down"):
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
