"""Run every tool, resource and prompt against a real Graylog and report each result.

Not part of CI: it needs a live Graylog with a GELF HTTP input. It sends a small
batch of fixture messages tagged with a run ID, waits until they are searchable,
then drives the current code in-process through the MCP server, as a client would.
The fixtures carry planted credentials; no tool or resource output may contain them.
Tools marked 5.1+ must work on Graylog 5.1 and later and return `Unsupported` on
older servers. Exit code 1 when any check fails.

usage (from the repository root):
  uv run python scripts/live_check.py --base-url http://localhost:29000 \\
      --user admin --password admin --gelf-url http://localhost:29001/gelf
  uv run python scripts/live_check.py --base-url http://localhost:19000 \\
      --api-token TOKEN --gelf-url http://localhost:12201/gelf --stream-id STREAM

A throwaway server: `python scripts/live/stack.py up 5.1`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from graylog_mcp.cli import wire
from graylog_mcp.interface.mcp_tools import build_server

ALL_MESSAGES = "000000000000000000000001"  # where a fresh server routes GELF messages
# Search Scripting time grouping: 5.1 rejects `timeunit`, 6.3 and 7.1 support it;
# 5.2 to 6.2 are untested, so (6, 0) is an assumption to confirm.
TIME_GROUPING_SINCE = (6, 0)
ERRORS = "level:3"
REDACTED = "[REDACTED]"
PLACEHOLDER = re.compile(r"\[REDACTED-\d+\]")
SESSIONS = 3  # fixture messages are spread evenly over this many session IDs

Check = Callable[[], str]  # returns a detail line; raises AssertionError on failure


class Run:
    def __init__(self, args: argparse.Namespace) -> None:
        auth = (args.api_token, "token") if args.api_token else (args.user, args.password)
        self.http = httpx.Client(
            base_url=args.base_url.rstrip("/"),
            auth=auth,
            headers={"Accept": "application/json", "X-Requested-By": "live-check"},
            timeout=30,
            verify=not args.no_verify_tls,
        )
        self.server = build_server(wire(self.http))
        self.stream = args.stream_id
        self.gelf_url = args.gelf_url
        self.run_id = f"run{int(time.time())}"
        self.query = f"live_check:{self.run_id}"
        self.version: tuple[int, int] = (0, 0)
        self.sent = 0
        self.password = f"pw-{self.run_id}-hunter2"
        self.db_password = f"db-{self.run_id}-s3cr3t"
        self.bearer = f"bearer{self.run_id}abcdefghijklmnop"
        self.github = f"ghp_{self.run_id}abcdefghijklmnop"
        self.sessions = [f"{self.run_id}-session-{n}" for n in range(SESSIONS)]
        self.planted = {
            self.password,
            self.db_password,
            self.bearer,
            self.github,
            *self.sessions,
        }

    # -- MCP calls -----------------------------------------------------------

    def tool(self, name: str, **arguments: Any) -> dict:
        result = asyncio.run(self.server.call_tool(name, arguments))
        text = result.content[0].text
        self.assert_redacted(name, text)
        return json.loads(text)

    def assert_redacted(self, source: str, text: str) -> None:
        leaked = sorted(secret for secret in self.planted if secret in text)
        assert not leaked, f"{source} leaked planted credentials {leaked}"

    def ok(self, name: str, **arguments: Any) -> dict:
        body = self.tool(name, **arguments)
        assert "error" not in body, f"{body.get('kind')}: {body.get('error')}"
        return body

    def scripting(self, name: str, **arguments: Any) -> dict | None:
        """A 5.1+ tool: must work on 5.1 and later, and be Unsupported before."""
        body = self.tool(name, **arguments)
        if self.version < (5, 1):
            assert body.get("kind") == "Unsupported", f"expected Unsupported, got {body}"
            return None
        assert "error" not in body, f"{body.get('kind')}: {body.get('error')}"
        return body

    # -- fixtures ------------------------------------------------------------

    def send_fixtures(self) -> None:
        now = time.time()
        messages = [("Checkout completed", 6, 200, None)] * 6
        messages += [("NullPointerException in CartService", 3, 500, "NullPointerException")] * 4
        messages += [("Payment provider timeout", 3, 504, "PaymentTimeout")] * 2
        for i, (text, level, code, error_type) in enumerate(messages):
            body = {
                "version": "1.1",
                "host": f"checkout-0{i % 2 + 1}",
                "short_message": text,
                "timestamp": round(now - 600 + i * 30, 3),
                "level": level,
                "_service": "checkout-api",
                "_http_response_code": code,
                "_duration_ms": 100 + i * 10,
                "_request_id": f"{self.run_id}-{i}",
                "_live_check": self.run_id,
                **({"_error_type": error_type} if error_type else {}),
                # Planted credentials: redaction must hide them in every output.
                "_password": self.password,
                "_session_id": self.sessions[i % SESSIONS],
                "_token_count": i,
                "_detail": (
                    f"login password={self.password} via postgres://app:{self.db_password}@db/x "
                    f"with Authorization: Bearer {self.bearer} and {self.github}"
                ),
            }
            request = urllib.request.Request(
                self.gelf_url, json.dumps(body).encode(), {"Content-Type": "application/json"}
            )
            urllib.request.urlopen(request, timeout=10).close()
        self.sent = len(messages)

    def wait_searchable(self, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        while True:
            body = self.tool(
                "count_matches", query=self.query, stream_id=self.stream, range_seconds=3600
            )
            if body.get("total_results") == self.sent:
                return
            if time.monotonic() > deadline:
                raise SystemExit(f"fixtures not searchable in time: {body}")
            time.sleep(5)

    # -- checks --------------------------------------------------------------

    def checks(self) -> list[tuple[str, Check]]:
        q, s, day = self.query, self.stream, 86400
        found: dict[str, Any] = {}

        def search() -> str:
            body = self.ok("find_recent_logs", query=q, stream_id=s, range_seconds=3600, limit=50)
            assert body["returned"] == self.sent, f"returned {body['returned']}"
            assert all("_index" in m and "_id" in m for m in body["messages"])
            found["error"] = next(m for m in body["messages"] if m.get("level") == 3)
            return f"{body['returned']} messages, each with _index and _id"

        def read_message() -> str:
            m = found["error"]
            body = self.ok("read_log_message", index=m["_index"], message_id=m["_id"])
            assert body.get("error_type"), f"custom fields missing: {sorted(body)}"
            return f"custom fields present ({body['error_type']})"

        def context() -> str:
            m = found["error"]
            body = self.ok(
                "message_context",
                index=m["_index"],
                message_id=m["_id"],
                stream_id=s,
                context_field="service",
                window_seconds=3600,
            )
            assert body["anchor"].get("_id") == m["_id"]
            return f"{len(body['before'])} before, {len(body['after'])} after"

        def redacted_search() -> str:
            body = self.ok("find_recent_logs", query=q, stream_id=s, range_seconds=3600, limit=1)
            [m] = body["messages"]
            assert (m.get("password"), m.get("session_id")) == (REDACTED, REDACTED), m
            assert m.get("token_count") not in (None, REDACTED), m
            assert m["detail"].count(REDACTED) == 4, m["detail"]
            return "credential fields and text redacted; token_count kept"

        def masked_rank() -> str:
            body = self.ok(
                "rank_field_values", query=q, field="session_id", stream_id=s, range_seconds=3600
            )
            counts = {v["value"]: v["count"] for v in body["top_values"]}
            assert all(PLACEHOLDER.fullmatch(v) for v in counts), counts
            assert sorted(counts.values()) == [self.sent // SESSIONS] * SESSIONS, counts
            return f"{len(counts)} session IDs masked, counts kept"

        def masked_exact() -> str:
            body = self.scripting(
                "exact_field_counts", query=q, field="session_id", stream_id=s, range_seconds=3600
            )
            if body is None:
                return "Unsupported (pre-5.1)"
            counts = {v["value"]: v["count"] for v in body["top_values"]}
            assert all(PLACEHOLDER.fullmatch(v) for v in counts), counts
            assert sorted(counts.values()) == [self.sent // SESSIONS] * SESSIONS, counts
            return f"{len(counts)} session IDs masked, counts kept"

        def masked_context() -> str:
            m = found["error"]
            body = self.ok(
                "message_context",
                index=m["_index"],
                message_id=m["_id"],
                stream_id=s,
                context_field="session_id",
                window_seconds=3600,
            )
            assert body["context_value"] == REDACTED, body["context_value"]
            neighbours = len(body["before"]) + len(body["after"])
            assert neighbours == self.sent // SESSIONS - 1, body
            return f"context value redacted; {neighbours} neighbours share the session"

        def graylog_data_redactions() -> str:
            """Reports, without failing, where Graylog's own data was redacted, since a
            real server may hold credentials; on a throwaway stack expect none."""
            calls = {
                "cluster_status": {},
                "server_capabilities": {},
                "browse_streams": {},
                "stream_rules": {"stream_id": s},
                "pipeline_rules": {},
                "index_sets": {},
                "lookup_tables": {},
                "alert_definitions": {},
                "input_status": {},
                "throughput": {},
                "system_notifications": {},
                "cluster_nodes": {},
            }
            hits = {}
            for name, arguments in calls.items():
                text = json.dumps(self.ok(name, **arguments))
                if REDACTED in text:
                    hits[name] = text.count(REDACTED)
            return f"redacted: {hits}" if hits else "nothing redacted"

        def fields() -> str:
            body = self.ok("discover_fields", stream_id=s, range_seconds=3600)
            by_name = {f["name"]: f for f in body["fields"]}
            assert "error_type" in by_name, f"error_type missing from {sorted(by_name)}"
            if body["source"] != "field types":
                return f"no field types API; {body['sampled_messages']} messages sampled"
            assert by_name["message"]["groupable"] is False
            if by_name["error_type"]["groupable"] is None:  # new field, not typed yet
                assert "no type yet" in body["note"], body
                return "typed; error_type not typed yet (noted), message not groupable"
            assert by_name["error_type"]["groupable"] is True
            return "typed: error_type groupable, message not"

        def unknown_stream() -> str:
            body = self.tool("discover_fields", stream_id="aaaaaaaaaaaaaaaaaaaaaaaa")
            assert body.get("kind") == "NotFound", body
            return "NotFound"

        def validate() -> str:
            if self.version < (5, 1):  # not verified before 5.1
                return "skipped before 5.1"
            body = self.ok("validate_query", query="nosuchfield:x", stream_id=s)
            assert body["status"] == "WARNING", body
            body = self.ok("validate_query", query="(level:3", stream_id=s)
            assert body["status"] == "ERROR" and not body["valid"], body
            return "unknown field warns, unbalanced bracket errors"

        def count() -> str:
            body = self.ok("count_matches", query=q, stream_id=s, range_seconds=3600)
            assert body["total_results"] == self.sent
            return f"{self.sent}"

        def rank() -> str:
            body = self.ok(
                "rank_field_values",
                query=f"{q} AND {ERRORS}",
                field="message",
                stream_id=s,
                range_seconds=3600,
            )
            top = body["top_values"][0]
            assert (top["value"], top["count"]) == ("NullPointerException in CartService", 4)
            return f"top message x{top['count']}"

        def capabilities() -> str:
            body = self.ok("server_capabilities")
            found = {name: c["available"] for name, c in body["capabilities"].items()}
            expected = {"aggregation": self.version >= (5, 1)}
            expected["time_grouping"] = self.version >= TIME_GROUPING_SINCE
            if self.version >= (5, 1):  # older servers are not verified for the rest yet
                expected |= dict.fromkeys(
                    ("field_types", "query_validation", "paginated_streams"), True
                )
            wrong = {k: found[k] for k, v in expected.items() if found[k] is not v}
            assert not wrong, f"expected {expected}, got {found}"
            return f"unsupported: {', '.join(body['unsupported_tools']) or 'none'}"

        def histogram() -> str:
            args = {"query": q, "stream_id": s, "range_seconds": 3600, "interval": "5m"}
            if (5, 1) <= self.version < TIME_GROUPING_SINCE:
                body = self.tool("count_over_time", **args)
                assert body.get("kind") == "Unsupported", body
                assert "cannot group by time" in body["error"], body
                return "Unsupported: no time grouping before " + ".".join(
                    map(str, TIME_GROUPING_SINCE)
                )
            body = self.scripting("count_over_time", **args)
            if body is None:
                return "Unsupported (pre-5.1)"
            assert body["total"] == self.sent, body["total"]
            return f"total {body['total']}"

        def exact() -> str:
            body = self.scripting(
                "exact_field_counts",
                query=q,
                field="error_type",
                stream_id=s,
                range_seconds=3600,
                top_n=5,
            )
            if body is None:
                return "Unsupported (pre-5.1)"
            # Graylog counts messages without the field as "(Empty Value)".
            counts = {v["value"]: v["count"] for v in body["top_values"]}
            counts.pop("(Empty Value)", None)
            assert counts == {"NullPointerException": 4, "PaymentTimeout": 2}, counts
            return str(counts)

        def compare() -> str:
            body = self.scripting(
                "compare_windows",
                query=f"{q} AND {ERRORS}",
                stream_id=s,
                window_seconds=3600,
                field="error_type",
            )
            if body is None:
                return "Unsupported (pre-5.1)"
            assert body["totals"]["current"] == 6, body["totals"]
            return f"{len(body['biggest_changes'])} values compared"

        def stats() -> str:
            body = self.scripting(
                "field_stats",
                query=q,
                field="duration_ms",
                stream_id=s,
                range_seconds=3600,
                percentiles="50",
            )
            if body is None:
                return "Unsupported (pre-5.1)"
            assert body["stats"]["count"] == self.sent, body["stats"]
            return f"avg {body['stats']['avg']}"

        def full_text_rejected() -> str:
            body = self.tool(
                "exact_field_counts", query=q, field="message", stream_id=s, range_seconds=3600
            )
            expected = "Unsupported" if self.version < (5, 1) else "InvalidRequest"
            assert body.get("kind") == expected, body
            return expected

        def incident() -> str:
            body = self.ok(
                "investigate_incident",
                query=f"{q} AND {ERRORS}",
                stream_id=s,
                range_seconds=3600,
                baseline_offset_seconds=day,
            )
            failed = {
                k: v["kind"]
                for k, v in _sections(body)
                if isinstance(v, dict)
                and "kind" in v
                and not (v["kind"] == "Unsupported" and self.version < (5, 1))
            }
            assert not failed, failed
            return "every section answered"

        def resources() -> str:
            uris = [str(r.uri) for r in asyncio.run(self.server.list_resources())]
            uris.append(f"graylog://streams/{s}/fields")
            for uri in uris:
                [content] = asyncio.run(self.server.read_resource(uri))
                self.assert_redacted(uri, str(content.content))
                if content.mime_type == "application/json":
                    body = json.loads(content.content)
                    assert "error" not in body, f"{uri}: {body}"
            return f"{len(uris)} read"

        def prompts() -> str:
            samples = {
                "investigate-service-errors": {"service": "checkout-api"},
                "compare-deployment-impact": {"deployed_at": "2026-01-01T00:00:00Z"},
                "find-missing-logs": {"source": "checkout-01"},
                "build-query": {"request": "5xx errors from checkout"},
                "explain-log-message": {"index": "i", "message_id": "m"},
            }
            tools = {t.name for t in asyncio.run(self.server.list_tools())}
            names = [p.name for p in asyncio.run(self.server.list_prompts())]
            for name in names:
                result = asyncio.run(self.server.get_prompt(name, samples.get(name, {})))
                text = " ".join(
                    m.content.text for m in result.messages if m.content.type == "text"
                )
                unknown = set(re.findall(r"`([^`]+)`", text)) - tools
                assert not unknown, f"{name} names unknown tools {unknown}"
            return f"{len(names)} rendered; every named tool exists"

        def between() -> str:
            now = datetime.now(timezone.utc)
            body = self.ok(
                "find_logs_between",
                query=q,
                stream_id=s,
                from_time=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                to_time=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
            assert body["total_results"] == self.sent, body["total_results"]
            return f"{body['total_results']} in the absolute window"

        def saved_search() -> str:
            searches = self.ok("saved_searches", limit=1)["saved_searches"]
            if not searches:  # a fresh stack has none; the check never creates config
                body = self.tool("run_saved_search", view_id="aaaaaaaaaaaaaaaaaaaaaaaa")
                assert body.get("kind") == "NotFound", body
                return "no saved searches; unknown ID gives NotFound"
            body = self.ok("run_saved_search", view_id=searches[0]["id"], stream_id=s, limit=1)
            return f"ran {searches[0]['title']!r}: {body['result']['total_results']} results"

        def lookup() -> str:
            tables = self.ok("lookup_tables", limit=1)["lookup_tables"]
            if not tables:
                body = self.tool("lookup_value", table="live-check-missing", key="x")
                assert body.get("kind") == "NotFound", body
                return "no lookup tables; unknown table gives NotFound"
            body = self.tool("lookup_value", table=tables[0]["name"], key="live-check")
            # A missing key is a normal answer; adapters that fail upstream report an error.
            assert "error" not in body or body["kind"] == "Backend", body
            return f"looked up in {tables[0]['name']!r}"

        plain = [
            ("browse_streams", lambda: f"{self.ok('browse_streams')['total']} streams"),
            ("recent_alerts", lambda: f"{self.ok('recent_alerts', range_seconds=day)['total']}"),
            ("alert_definitions", lambda: f"{self.ok('alert_definitions')['total']}"),
            ("saved_searches", lambda: f"{self.ok('saved_searches')['total']}"),
            ("dashboards", lambda: f"{self.ok('dashboards')['total']}"),
            ("lookup_tables", lambda: f"{self.ok('lookup_tables')['total']}"),
            ("index_sets", lambda: f"{self.ok('index_sets')['total']}"),
            ("pipeline_rules", lambda: f"{self.ok('pipeline_rules')['total']}"),
            ("stream_rules", lambda: self.ok("stream_rules", stream_id=s)["summary"]),
            ("input_status", lambda: f"{self.ok('input_status')['total']} inputs"),
            ("throughput", lambda: str(self.ok("throughput")["cluster"])),
            ("system_notifications", lambda: f"{self.ok('system_notifications')['total']}"),
            ("cluster_nodes", lambda: f"{self.ok('cluster_nodes')['healthy']} healthy"),
        ]
        return [
            ("search with _index/_id", search),
            ("find_logs_between", between),
            ("read_log_message", read_message),
            ("message_context", context),
            ("redacted search results", redacted_search),
            ("rank_field_values masks session_id", masked_rank),
            ("exact_field_counts masks (5.1+)", masked_exact),
            ("message_context redacts context", masked_context),
            ("Graylog data redactions", graylog_data_redactions),
            ("discover_fields", fields),
            ("discover_fields unknown stream", unknown_stream),
            ("validate_query", validate),
            ("count_matches", count),
            ("rank_field_values on message", rank),
            ("server_capabilities", capabilities),
            ("count_over_time (5.1+)", histogram),
            ("exact_field_counts (5.1+)", exact),
            ("compare_windows per value (5.1+)", compare),
            ("field_stats (5.1+)", stats),
            ("full-text field rejected clearly", full_text_rejected),
            ("investigate_incident", incident),
            *plain,
            ("run_saved_search", saved_search),
            ("lookup_value", lookup),
            ("resources", resources),
            ("prompts", prompts),
        ]


def _sections(body: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every nested section of a composite result, so errors anywhere are found."""
    if not isinstance(body, dict):
        return []
    found = [(path, body)] if path else []
    for key, value in body.items():
        found += _sections(value, f"{path}.{key}" if path else key)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-token")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--gelf-url", required=True)
    parser.add_argument("--stream-id", default=ALL_MESSAGES)
    parser.add_argument("--no-verify-tls", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)

    run = Run(args)
    version = run.ok("cluster_status")["version"]
    run.version = tuple(int(p) for p in re.findall(r"\d+", version)[:2])
    print(f"Graylog {version}, stream {run.stream}, fixtures tagged {run.query}")
    run.send_fixtures()
    run.wait_searchable()

    failures = 0
    for name, check in run.checks():
        try:
            detail = check()
            print(f"PASS  {name:34} {detail}")
        except Exception as exc:  # report every failure, keep going
            failures += 1
            print(f"FAIL  {name:34} {type(exc).__name__}: {exc}")
    print(f"\n{failures} failed" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
