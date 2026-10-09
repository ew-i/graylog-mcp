"""MCP prompts: recommended tool sequences for common Graylog workflows.

Prompts only render instructions; they call nothing. In the instruction texts,
backticks mark tool names and nothing else, so tests can check every name exists.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .query_guide import QUERY_SYNTAX

_RULES = (
    "Rules: the tools are read-only. Do not guess field names; check them with "
    "`discover_fields`. Group and compare by structured fields such as an error type; "
    "the full-text message field cannot be grouped on the server, so rank message texts "
    "with `rank_field_values` instead. A result of {kind: ...} is an error to explain, "
    "not data. "
    "Cite counts, times and message IDs as evidence, and say what you could not verify."
)


def _stream_step(stream_id: str) -> str:
    if stream_id:
        return f'Use stream_id "{stream_id}".'
    return "No stream was given: call `browse_streams` and pick the stream that fits, saying why."


def register(server: MCPServer) -> None:
    @server.prompt(
        name="triage-production-alert",
        title="Triage a production alert",
        description="Decide whether an alert is real, how big it is and where to look first.",
    )
    def triage_production_alert(
        alert: Annotated[
            str, Field(description="Alert title, event ID or definition ID; empty for the newest")
        ] = "",
        range_seconds: Annotated[int, Field(description="How far back to look for it")] = 86400,
    ) -> str:
        target = f'the alert matching "{alert}"' if alert else "the newest alert"
        return f"""Triage {target}.

1. Call `recent_alerts` with range_seconds={range_seconds} and find {target}. If none
   matches, call it again with alerts_only=false, list the closest events and stop.
2. Call `alert_definitions` for its definition: query, streams, search window and
   priority. If the definition has no streams, call `browse_streams` and ask which to use.
3. On each stream, call `count_over_time` with the definition query over a range that
   includes the alert, and `compare_windows` for the same query against yesterday.
   Is this a spike, a slow rise, or normal noise?
4. Call `find_logs_between` with the definition query around the alert time, then
   `message_context` on one representative message.
5. Call `exact_field_counts` on fields such as source or service (check names with
   `discover_fields`) to see whether one host or service dominates.

Report: what fired and when, real problem or noise, scope (sources, services, volume
versus baseline), the most likely cause with evidence, and the next checks.
{_RULES}"""

    @server.prompt(
        name="investigate-service-errors",
        title="Investigate service errors",
        description="Find what a service's errors are, when they started and why.",
    )
    def investigate_service_errors(
        service: Annotated[str, Field(description="Service, application or host name")],
        stream_id: Annotated[str, Field(description="Stream to search; empty to choose")] = "",
        range_seconds: Annotated[int, Field(description="Look-back window")] = 3600,
    ) -> str:
        return f"""Investigate errors from "{service}" over the last {range_seconds} seconds.

1. {_stream_step(stream_id)}
2. Call `discover_fields` to learn which field names the service ("service", "application",
   "kubernetes_container_name", ...) and the severity ("level", "severity", ...).
3. Build an error query for "{service}" from those fields and check it with
   `count_matches`. If it matches nothing, adjust it and say how.
4. Call `investigate_incident` with that query, the stream and range_seconds={range_seconds}.
5. Group the errors: `exact_field_counts` on an error-type or exception field if one
   exists, otherwise `rank_field_values` on the message field. Then `count_over_time`
   to find when they started.
6. For the largest group, call `read_log_message` and `message_context` with the
   _index and _id from a search result.

Report: the main error groups with counts, when they started, whether volume is above
baseline, the likely cause with evidence, and next steps.
{_RULES}"""

    @server.prompt(
        name="compare-deployment-impact",
        title="Compare deployment impact",
        description="Compare errors and volume after a deployment with the time before it.",
    )
    def compare_deployment_impact(
        deployed_at: Annotated[
            str, Field(description="Deployment time, ISO-8601 UTC, e.g. 2026-03-15T10:00:00Z")
        ],
        service: Annotated[str, Field(description="Deployed service; empty for all")] = "",
        stream_id: Annotated[str, Field(description="Stream to compare; empty to choose")] = "",
    ) -> str:
        scope = f'the service "{service}"' if service else "all services in the stream"
        return f"""Compare {scope} before and after the deployment at {deployed_at}.

1. {_stream_step(stream_id)}
2. Call `discover_fields` for the service and severity field names, and build an
   error query{f' for "{service}"' if service else ""} and a query for all its messages.
3. Let T be the seconds from {deployed_at} until now. Call `compare_windows` with
   window_seconds=T and baseline_offset_seconds=T, once with the error query and once
   with the all-messages query, so the time since the deployment is compared with an
   equally long time before it. Add field set to an error-type or exception field to
   find errors that are new since the deployment; without one, compare message texts
   from `rank_field_values` in both periods.
4. Call `count_over_time` over a range covering both periods to see whether the
   change starts exactly at the deployment.
5. Call `find_logs_between` from {deployed_at} on with the error query and read the
   newest examples of any new error with `read_log_message`.

Report: error and volume change with numbers, errors that are new since the
deployment, whether the change lines up with the deployment time, and a verdict
(no impact, degraded, or broken) with evidence.
{_RULES}"""

    @server.prompt(
        name="find-missing-logs",
        title="Find missing logs",
        description="Explain why a source's logs are missing: unsent, lost, misrouted, expired.",
    )
    def find_missing_logs(
        source: Annotated[str, Field(description="Host, service or application that is missing")],
        stream_id: Annotated[
            str, Field(description="Stream where the logs are expected; empty to choose")
        ] = "",
    ) -> str:
        return f"""Find out why logs from "{source}" are missing.

1. {_stream_step(stream_id)}
2. Call `discover_fields` to learn which field identifies "{source}" (source, host,
   service, ...), then `count_over_time` for it over the last 7 days
   (range_seconds=604800): did it never arrive, or stop at a certain time?
3. If it is not in this stream, call `browse_streams` and `count_matches` for the same
   query on the other streams; the default "All messages" stream has stream_id
   "000000000000000000000001" and also holds messages no other stream removed.
4. If it arrives in another stream, call `read_log_message` on one of its messages and
   `stream_rules` for the expected stream: which rule fails to match, and why?
5. If it arrives nowhere, check Graylog's side: `input_status` (only_problems=true),
   `throughput` and `system_notifications`. With all of those healthy, the sender or
   the network between it and Graylog is the likely cause.
6. If only older logs are missing, call `index_sets` for the stream to check retention,
   and `pipeline_rules` for the stream to look for rules that drop or reroute messages.

Report: whether Graylog ever received logs from "{source}", where they end up or stop,
since when, and the cause with evidence (sender, input, routing, pipeline or retention).
{_RULES}"""

    @server.prompt(
        name="build-query",
        title="Build a Graylog query",
        description="Turn a plain-language request into a tested Graylog query.",
    )
    def build_query(
        request: Annotated[
            str,
            Field(description='What to find, e.g. "5xx errors from checkout in the last hour"'),
        ],
        stream_id: Annotated[str, Field(description="Stream to query; empty to choose")] = "",
    ) -> list[dict[str, Any]]:
        steps = f"""Write a Graylog query for this request: "{request}"

The query syntax reference is attached above.

1. {_stream_step(stream_id)}
2. Call `discover_fields` (range_seconds=2592000) for the real field names, and
   `exact_field_counts` on candidate fields to see their actual values (for example
   whether level is a number or a word).
3. Write the query with those fields. Turn any time in the request into range_seconds,
   or into from_time and to_time for `find_logs_between`.
4. Test it with `count_matches`. If it matches nothing or far too much, revise it (at
   most three times) and say what you changed.
5. Show up to three matching messages with `find_recent_logs` (limit=3) so the user
   can check that they are the right ones.

Report: the final query in a code block, the stream and time range, the match count,
the fields and values you relied on, and a narrower or broader variant if useful.
{_RULES}"""
        return [
            {
                "role": "user",
                "content": {
                    "type": "resource",
                    "resource": {
                        "uri": "graylog://guide/query-syntax",
                        "mimeType": "text/markdown",
                        "text": QUERY_SYNTAX,
                    },
                },
            },
            {"role": "user", "content": steps},
        ]

    @server.prompt(
        name="explain-log-message",
        title="Explain a log message",
        description="Explain one message: what it means, what surrounds it, how often it occurs.",
    )
    def explain_log_message(
        index: Annotated[str, Field(description="The message's _index from a search result")],
        message_id: Annotated[str, Field(description="The message's _id from a search result")],
        stream_id: Annotated[
            str, Field(description="Stream of the message; empty to read it from the message")
        ] = "",
    ) -> str:
        if stream_id:
            stream = f'Use stream_id "{stream_id}".'
        else:
            stream = (
                "No stream was given: call `read_log_message` with fields set to "
                '"streams" and use the first stream ID it lists.'
            )
        return f"""Explain the log message with _index "{index}" and _id "{message_id}".

1. Call `read_log_message` with that _index and _id for all of its fields.
2. {stream}
3. Call `message_context` (before=10, after=10) to see what happened around it on the
   same source; try context_field set to a request or trace ID field if it has one.
   If it finds no neighbours, retry with a larger window_seconds (up to 86400).
4. Pick the constant part of its message text (leave out IDs, numbers and timestamps),
   then call `count_over_time` for it over the last 7 days (range_seconds=604800) and
   `exact_field_counts` on the source field: is it new, recurring or rising, and where?

Report: what the message means in plain words, how severe it is, what led up to it and
followed it, how often and where it occurs, and whether it needs action.
{_RULES}"""

    @server.prompt(
        name="daily-briefing",
        title="Daily briefing",
        description="What changed in the last 24 hours: alerts, volume, new and rising errors.",
    )
    def daily_briefing(
        stream_id: Annotated[
            str, Field(description="Stream to brief on; empty for the main streams")
        ] = "",
    ) -> str:
        if stream_id:
            streams = f'Use stream_id "{stream_id}".'
        else:
            streams = (
                "No stream was given: call `browse_streams` and brief on the streams that "
                "carry application logs (at most five), saying which you chose."
            )
        return f"""Give a briefing on the last 24 hours compared with the day before.

1. Call `recent_alerts` (range_seconds=86400) and `system_notifications`.
2. {streams}
3. For each stream, call `compare_windows` with query "*" (window_seconds=86400,
   baseline_offset_seconds=86400) for overall volume.
4. Call `discover_fields` for the severity field and build an error query, then call
   `compare_windows` with it twice: with field set to the source field (who got worse)
   and with field set to an error-type field (errors marked new). Without such a
   field, use `rank_field_values` on the message field for each day instead.
5. For the largest new or rising error, show one example via `find_recent_logs`.

Report: a three-line headline first, then alerts, volume changes, new and rising
errors with counts, and the sources behind them. Say "quiet day" if nothing changed.
{_RULES}"""

    @server.prompt(
        name="summarize-cluster-health",
        title="Summarize cluster health",
        description="One overview of nodes, inputs, throughput, notifications and alerts.",
    )
    def summarize_cluster_health() -> str:
        return f"""Summarize the health of this Graylog cluster.

1. Call `cluster_status` and `cluster_nodes`: version, leader, unhealthy nodes.
2. Call `input_status` (only_problems=true) and `throughput`: failed inputs, buffer
   usage and whether the journal is growing.
3. Call `system_notifications` and `recent_alerts` (the last 24 hours).
4. Call `index_sets` for retention and writability problems.

Report a status of OK, DEGRADED or DOWN first, then each problem with its evidence,
ordered by impact, and what to check next. If everything is healthy, say so briefly.
{_RULES}"""
