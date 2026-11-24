# graylog-mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server for
Graylog. It lets an AI assistant search and analyse logs, read alerts, run saved
searches and check cluster health and configuration, without changing anything
in Graylog.

[![build](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml) [![coverage](https://raw.githubusercontent.com/ew-i/badges/main/graylog-mcp/coverage.svg)](https://github.com/ew-i/graylog-mcp) [![codeql](https://github.com/ew-i/graylog-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/codeql.yml) ![Python](https://img.shields.io/badge/python-3.11_%E2%80%93_3.15-yellow?labelColor=blue) ![Graylog](https://img.shields.io/badge/graylog-5.1_%E2%80%93_7.2-08c2fd)

- **32 tools** for search, analysis, alerts, saved views, operations, configuration
  and one-call incident triage
- **9 resources** (8 fixed and 1 template) that attach reference data such as
  streams, fields and query syntax without a tool call
- **8 prompts** for common workflows, such as triaging an alert or comparing
  before and after a deploy
- **stdio or Streamable HTTP** transport, run locally with `uv` or in Docker
- **Credential redaction** on every response, and stable cursor-based paging

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Connect an MCP client](#connect-an-mcp-client)
- [Tools](#tools)
- [Resources](#resources)
- [Prompts](#prompts)
- [Configuration](#configuration)
- [Security](#security)
- [Development](#development)
- [Architecture](#architecture)
- [CI](#ci)
- [License](#license)

## Requirements

- A Graylog server, 5.1 to 7.2. Some analysis tools need newer versions; see
  [Version support](#version-support).
- A Graylog API token for a user who can read the streams you want to query.
  Other tools need further read permissions; see the **Needs** column under
  [Tools](#tools).
- One of:
  - [uv](https://docs.astral.sh/uv/) and Python 3.11 or later
  - Docker

## Quick start

```bash
git clone https://github.com/ew-i/graylog-mcp.git
cd graylog-mcp
cp .env.example .env          # then set GRAYLOG_BASE_URL and GRAYLOG_API_TOKEN
uv sync
uv run graylog-mcp            # or: uv run python -m graylog_mcp.cli
```

The server speaks MCP over stdio by default, so it waits for a client on
standard input. Usually you don't start it by hand: point your MCP client at it
(see the next section). Run `uv run graylog-mcp --help` to list all options.

`GRAYLOG_BASE_URL` must use `https://`. For a local Graylog over plain HTTP, also
set `GRAYLOG_ALLOW_INSECURE_HTTP=true`.

## Connect an MCP client

### Local install (stdio)

After `uv sync`, the executable is at `.venv/bin/graylog-mcp` in the project
directory. Replace `/absolute/path/to/graylog-mcp` below with that directory.

**Claude Desktop, Cursor and other clients using the `mcpServers` format:**

```json
{
  "mcpServers": {
    "graylog": {
      "command": "/absolute/path/to/graylog-mcp/.venv/bin/graylog-mcp",
      "env": {
        "GRAYLOG_BASE_URL": "https://graylog.example.com",
        "GRAYLOG_API_TOKEN": "your-graylog-api-token"
      }
    }
  }
}
```

**OpenCode** (`~/.config/opencode/opencode.json` or a project `opencode.json`):

```json
{
  "mcp": {
    "graylog": {
      "type": "local",
      "command": ["/absolute/path/to/graylog-mcp/.venv/bin/graylog-mcp"],
      "environment": {
        "GRAYLOG_BASE_URL": "https://graylog.example.com",
        "GRAYLOG_API_TOKEN": "your-graylog-api-token"
      },
      "enabled": true
    }
  }
}
```

### Docker (stdio)

Build the image from the project root:

```bash
docker build -t graylog-mcp .
```

The client starts a container per session. `-i` keeps standard input open,
which stdio needs. `-e NAME` without a value passes the variable through from
the client's environment, so the token does not show up in the process list:

```json
{
  "mcpServers": {
    "graylog": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "GRAYLOG_BASE_URL",
        "-e", "GRAYLOG_API_TOKEN",
        "graylog-mcp"
      ],
      "env": {
        "GRAYLOG_BASE_URL": "https://graylog.example.com",
        "GRAYLOG_API_TOKEN": "your-graylog-api-token"
      }
    }
  }
}
```

For OpenCode, use the same `docker` command as a `command` array and put the
variables under `environment`.

### Streamable HTTP

Start the server as a long-running HTTP endpoint:

```bash
uv run graylog-mcp --transport streamable-http          # http://127.0.0.1:8000/mcp
```

Or in Docker, listening on all interfaces inside the container:

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  -e GRAYLOG_BASE_URL=https://graylog.example.com \
  -e GRAYLOG_API_TOKEN \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  graylog-mcp
```

Then configure the client with the URL:

```json
{
  "mcpServers": {
    "graylog": { "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

The HTTP endpoint has no authentication of its own: anyone who can reach it can
read Graylog with the configured token. Before exposing it beyond localhost,
read [HTTP exposure](#http-exposure).

## Tools

All tools are read-only. **Needs** lists the Graylog permissions a tool requires
beyond reading streams; without them it returns `{"kind": "AccessDenied", ...}`.

| Area          | Tool                                     | What it does                                                            | Needs               |
|---------------|------------------------------------------|-------------------------------------------------------------------------|---------------------|
| Search        | `cluster_status`                         | Version, node, timezone, processing state                               |                     |
|               | `server_capabilities`                    | Version, optional APIs it offers, tools it cannot serve                 |                     |
|               | `browse_streams`                         | Streams you may query, with IDs; call this first                        |                     |
|               | `find_recent_logs` / `find_logs_between` | Message search; each hit carries `_index` and `_id`                     |                     |
|               | `read_log_message`                       | One message in full                                                     |                     |
|               | `rank_field_values`                      | Top values from a sample of up to 1000 newest matches                   |                     |
|               | `discover_fields`                        | Fields with type, and whether groupable or numeric                      |                     |
|               | `validate_query`                         | Syntax and unknown-field check, without searching                       |                     |
| Analysis      | `count_matches`                          | Number of hits only                                                     |                     |
|               | `count_over_time`                        | Histogram, optionally split by a field                                  | Graylog 6.3+        |
|               | `exact_field_counts`                     | Exact top values, counted server-side                                   | Graylog 5.1+        |
|               | `compare_windows`                        | Now vs. an earlier window; totals and per-value changes                 | per-value: 5.1+     |
|               | `field_stats`                            | count/avg/min/max/sum/percentiles of a numeric field                    | Graylog 5.1+        |
|               | `message_context`                        | Messages just before/after one message (same source/pod)                |                     |
| Alerts        | `recent_alerts`                          | Alerts/events raised in a window                                        | events read         |
|               | `alert_details`                          | One alert with its definition, originating and matching messages        | events, stream read |
|               | `find_related_alerts`                    | Nearby events with the same definition or group-by values               | events read         |
|               | `alert_definitions`                      | What each event definition checks and how often                         | events read         |
| Saved views   | `saved_searches` / `dashboards`          | List them                                                               | view read           |
|               | `run_saved_search`                       | Run a saved search's query, stream and range                            | view read           |
| Operations    | `input_status`                           | Inputs and their per-node state                                         | inputs read         |
|               | `throughput`                             | msg/s in/out, buffer usage, journal backlog                             | metrics read        |
|               | `system_notifications`                   | Graylog's own warnings, urgent first                                    | notifications read  |
|               | `cluster_nodes`                          | Nodes, leader, lifecycle, health                                        | cluster read        |
| Configuration | `stream_rules`                           | A stream's routing rules in plain words                                 | stream read         |
|               | `pipeline_rules`                         | Pipelines (per stream), stages and rule source                          | pipeline read       |
|               | `index_sets`                             | Rotation/retention, i.e. how far back you can search                    | index sets read     |
|               | `lookup_tables` / `lookup_value`         | List lookup tables; resolve a key via one                               | lookup tables read  |
| Investigation | `investigate_incident`                   | Composite incident triage across health, logs, alerts and configuration | varies              |

### Version support

Tools marked **5.1+** use Graylog's Search Scripting API. On older servers they
return `{"kind": "Unsupported", ...}`; all other tools keep working.
`count_over_time` also needs time grouping, which the 5.1 API lacks; it is
verified on 6.3 and 7.2 (5.2 to 6.2 are untested).

`server_capabilities` checks each optional API on the connected server (one
request each; none runs a search) and lists the tools that return `Unsupported`
there. Once an API is known to be missing, tools that need it answer
`Unsupported` without asking Graylog again, for as long as resources are cached
(`MCP_RESOURCE_CACHE_SECONDS`).

### Paging

Logs, events, streams, dashboards, saved searches, alert definitions and lookup
tables are paged. Responses carry `limit`, `next_cursor`, `has_more` and
`truncated`; pass `next_cursor` back to get the next page.

- The cursor carries the original request, so later pages repeat it exactly.
- A relative time range is pinned to absolute timestamps on the first page, so
  pages don't shift while new messages arrive.
- `truncated` (with a `truncation` explanation) means results are missing that
  paging alone won't recover: the requested limit exceeded the maximum page
  size, or the matches go deeper than Graylog can page (10,000 for logs and
  events).

### Errors

Tools never raise. A failure returns `{"error": ..., "kind": ...}`, for example
`AccessDenied` or `Unsupported`. An unexpected internal error has kind
`Internal` and is logged with its traceback to stderr. In
`investigate_incident` it replaces only the failing section.

## Resources

Resources expose stable reference data that a client can attach as context
without a tool call. Graylog-backed resources return the same redacted JSON as
the matching tool, and failures as `{"error": ..., "kind": ...}`.

| Resource                               | Contents                                            | Same as               |
|----------------------------------------|-----------------------------------------------------|-----------------------|
| `graylog://streams`                    | Streams you may query, with IDs                     | `browse_streams`      |
| `graylog://saved-searches`             | Saved searches, with IDs for `run_saved_search`     | `saved_searches`      |
| `graylog://streams/{stream_id}/fields` | A stream's fields with type and capabilities (30 d) | `discover_fields`     |
| `graylog://cluster/status`             | Version, cluster identity, processing state         | `cluster_status`      |
| `graylog://cluster/capabilities`       | Optional APIs offered, tools this server can't run  | `server_capabilities` |
| `graylog://alerts/definitions`         | Event/alert definitions                             | `alert_definitions`   |
| `graylog://config/index-sets`          | Rotation and retention                              | `index_sets`          |
| `graylog://config/pipelines`           | Pipelines, stages and rules                         | `pipeline_rules`      |
| `graylog://guide/query-syntax`         | Query syntax and how to use these tools             | static Markdown       |

- `graylog://streams/{stream_id}/fields` is a resource template; clients list it
  separately from the fixed resources.
- Successful reads are cached per URI for 60 seconds by default
  (`MCP_RESOURCE_CACHE_SECONDS`; `0` disables). Failures are not cached.
- Catalogue resources return the first 200 entries with the usual
  `has_more`/`next_cursor`; use the tool to page further.
- Tools are never cached. Use them when the data must be current, such as
  cluster health.

## Prompts

Prompts are workflow templates the user picks in the client, often as a slash
command. Each renders step-by-step instructions naming the tools to call and
what to report; prompts never call Graylog themselves.

| Prompt                       | Arguments                                          | Workflow                                           |
|------------------------------|----------------------------------------------------|----------------------------------------------------|
| `triage-production-alert`    | `alert`, `range_seconds`                           | Real or noise, scope, likely cause of an alert     |
| `investigate-service-errors` | `service` (required), `stream_id`, `range_seconds` | Error groups, onset and cause for a service        |
| `compare-deployment-impact`  | `deployed_at` (required), `service`, `stream_id`   | Errors and volume before versus after a deploy     |
| `find-missing-logs`          | `source` (required), `stream_id`                   | Never sent, lost, misrouted or expired             |
| `build-query`                | `request` (required), `stream_id`                  | Plain language to a tested query (guide attached)  |
| `explain-log-message`        | `index`, `message_id` (both required), `stream_id` | Meaning, surrounding messages, how often it occurs |
| `daily-briefing`             | `stream_id`                                        | Last 24 h: alerts, volume, new and rising errors   |
| `summarize-cluster-health`   |                                                    | OK, DEGRADED or DOWN with evidence                 |

Without a `stream_id`, a prompt tells the model to pick one with
`browse_streams`; `explain-log-message` reads it from the message instead.

## Configuration

Every setting can come from a CLI flag, an environment variable or a `.env`
file in the working directory. Precedence, highest first: CLI flag, process
environment, `.env`, default. A CLI flag replaces its environment value
entirely, so an invalid environment value doesn't block startup when the
matching flag is given. See [`.env.example`](.env.example) for a template.

### Graylog connection

| Variable                      | CLI flag                                             | Default | Description                                           |
|-------------------------------|------------------------------------------------------|---------|-------------------------------------------------------|
| `GRAYLOG_BASE_URL`            | `--base-url`                                         | –       | **Required.** Graylog URL, `https://` unless allowed  |
| `GRAYLOG_API_TOKEN`           | `--api-token`                                        | –       | **Required.** Graylog API token                       |
| `GRAYLOG_VERIFY_TLS`          | `--verify-tls` / `--no-verify-tls`                   | `true`  | Verify the Graylog TLS certificate                    |
| `GRAYLOG_ALLOW_INSECURE_HTTP` | `--allow-insecure-http` / `--no-allow-insecure-http` | `false` | Allow an `http://` Graylog URL, for local development |
| `GRAYLOG_TIMEOUT_SECONDS`     | `--timeout-seconds`                                  | `30`    | HTTP request timeout                                  |

### MCP server

| Variable                              | CLI flag                                                                     | Default     | Description                                                       |
|---------------------------------------|------------------------------------------------------------------------------|-------------|-------------------------------------------------------------------|
| `MCP_TRANSPORT`                       | `--transport`                                                                | `stdio`     | `stdio` or `streamable-http`                                      |
| `MCP_HOST`                            | `--host`                                                                     | `127.0.0.1` | HTTP listen host                                                  |
| `MCP_PORT`                            | `--port`                                                                     | `8000`      | HTTP listen port                                                  |
| `MCP_STREAMABLE_HTTP_PATH`            | `--streamable-http-path`                                                     | `/mcp`      | Streamable HTTP endpoint path                                     |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | `--enable-dns-rebinding-protection` / `--no-enable-dns-rebinding-protection` | `false`     | Validate HTTP `Host` and `Origin` headers                         |
| `MCP_ALLOWED_HOSTS`                   | `--allowed-hosts`                                                            | empty       | Comma-separated allowed `Host` values                             |
| `MCP_ALLOWED_ORIGINS`                 | `--allowed-origins`                                                          | empty       | Comma-separated allowed `Origin` values                           |
| `MCP_REDACT_FIELDS`                   | `--redact-fields`                                                            | empty       | Comma-separated extra field names to redact                       |
| `MCP_RESOURCE_CACHE_SECONDS`          | `--resource-cache-seconds`                                                   | `60`        | Resource cache lifetime; `0` disables                             |
| `MCP_LOG_LEVEL`                       | `--log-level`                                                                | `INFO`      | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`                 |

Example: `uv run graylog-mcp --log-level DEBUG --timeout-seconds 60`.

## Security

### Token scope

The server can read everything the API token can read. Give the token's user
only the stream and read permissions it needs. Redaction (below) doesn't stop a
client from narrowing down a secret through repeated searches such as
`password:a*`; restricting stream permissions does.

### Redaction

Responses are redacted before they leave the server:

- Values under sensitive key names, such as any key containing `password`,
  `secret`, `token` or `api_key`, or ending in `auth`, `session` or `cookie`.
  Keys that only describe a secret (`token_count`, `token_type`) and
  true/false or null values are kept.
- Credential patterns in text: labelled values (`password=…`, `"token": "…"`,
  `Authorization: …`), passwords in URLs of any scheme, bearer tokens, JWTs,
  private keys, webhook URLs, Azure keys and SAS signatures, and well-known
  token formats (AWS, GitHub, GitLab, Slack, Google, Stripe, OpenAI/Anthropic,
  npm, PyPI).
- The configured `GRAYLOG_API_TOKEN`, wherever its exact value appears.
- Values of a sensitive field that a tool reports on, such as
  `rank_field_values` on `session_id`, become numbered placeholders
  (`[REDACTED-1]`), so counts and repeats stay visible.

Add application-specific field names with `MCP_REDACT_FIELDS`. They match key
names exactly, ignoring case and punctuation. Built-in rules cannot be
disabled.

### HTTP exposure

The Streamable HTTP endpoint does not authenticate clients. Keep it on
`127.0.0.1` (the default), or put it behind a reverse proxy or network policy
that does. When it must be reachable under a hostname, also enable
DNS-rebinding protection and list the `Host` and browser `Origin` values
clients send:

```bash
uv run graylog-mcp \
  --transport streamable-http \
  --host 0.0.0.0 \
  --enable-dns-rebinding-protection \
  --allowed-hosts mcp.example.com:8000 \
  --allowed-origins https://app.example
```

With protection enabled, every request must carry an allowed `Host` and, when
present, an allowed `Origin`. Values support the MCP SDK's `:*` port wildcard,
such as `localhost:*` or `https://app.example:*`.

The Docker image runs as an unprivileged user (`mcp`, UID 1000).

## Development

```bash
uv sync --dev
uv run pytest                    # or, stdlib only: uv run python -m unittest discover -s tests -t .
uv run pytest --cov=src/graylog_mcp --cov-report=term-missing   # CI requires 100 %
uv run ruff check src tests      # PEP 8 and static checks
uv run ruff format src tests     # format; CI runs `ruff format --check`
uv lock --check                  # verify the lockfile matches pyproject.toml
```

Style: PEP 8 via ruff (`E`, `W`, `F`, `N`, `I`, `B`, `UP`, `SIM`, `C4`),
99-character lines, `X | None` annotations, exceptions named `...Error`.
Dependencies are pinned to exact versions; update them with `uv lock`.

### Live check

`scripts/live_check.py` runs every tool, resource and prompt against a real
Graylog, and checks that planted credentials never appear in any output. It is
opt-in and not part of CI.

`scripts/live/stack.py` starts a throwaway Graylog with OpenSearch and MongoDB
in Docker, listening on localhost only, with the login `admin` / `admin`. It
also creates a GELF HTTP input and a `reader` user who can read only the default
stream, and prints the matching `live_check.py` arguments when ready.

| Version | Web / API                | GELF HTTP                     |
|---------|--------------------------|-------------------------------|
| `5.1`   | `http://localhost:29000` | `http://localhost:29001/gelf` |
| `6.3`   | `http://localhost:39000` | `http://localhost:39001/gelf` |
| `7.1`   | `http://localhost:49000` | `http://localhost:49001/gelf` |
| `7.2`   | `http://localhost:59000` | `http://localhost:59001/gelf` |

```bash
python scripts/live/stack.py up 7.2
uv run python scripts/live_check.py --base-url http://localhost:59000 \
    --user admin --password admin --gelf-url http://localhost:59001/gelf \
    --reader reader:reader-password-1 --create-alerts
python scripts/live/stack.py down 7.2      # removes the stack and its volumes
```

`--create-alerts` creates two event definitions so the alert tools have real
events to read; use it on throwaway servers only. Against your own server, pass
`--api-token`, your GELF HTTP input URL and the `--stream-id` its messages land
in.

## Architecture

The code follows a layered (ports and adapters) design: the domain has no I/O,
use cases depend only on ports, and Graylog and MCP are adapters at the edges.

```
src/graylog_mcp/
  domain/           pure models, invariants, analysis; no I/O, no frameworks
    models.py         LogQuery, time windows, LogEntry, Stream, ClusterInfo
    capabilities.py   optional Graylog APIs (Capability)
    aggregation.py    AggregationQuery, groupings, metrics, intervals
    analysis.py       sampled ranking, field discovery
    errors.py         error hierarchy used across all layers
    alerts.py  views.py  operations.py  configuration.py  timeutil.py
  application/      use cases and policy; depend only on domain and ports
    ports.py          StreamCatalog, MessageStore (= LogStore), AggregationStore,
                      CapabilityStore, AlertStore, ViewStore, SystemStore, ConfigStore
    policy.py         limits, clamping, argument parsing
    pagination.py     cursors
    service.py        LogService (search)
    incident.py       investigate_incident composition
    analytics.py  alerts.py  saved_views.py  operations.py  configuration.py
    capabilities.py   CapabilityService (server_capabilities)
  infrastructure/   Graylog REST adapters, one per port
    graylog_api.py    shared HTTP client: auth, error mapping, JSON
    graylog_capabilities.py  probes and remembers which optional APIs exist
    graylog_http.py  graylog_analytics.py  graylog_alerts.py
    graylog_views.py  graylog_system.py  graylog_config.py
    config.py         Pydantic settings from CLI, environment and .env
  interface/        MCP surface
    mcp_tools.py      ServiceBundle and build_server
    tools_*.py        tools, one module per area
    tool_annotations.py  read-only hints shared by every tool
    resources.py      graylog:// resources with a short-lived cache
    prompts.py        workflow prompts
    query_guide.py    text of graylog://guide/query-syntax
    redaction.py      credential redaction
    responses.py      response and error shaping
  cli.py            argparse entrypoint and composition root
tests/              domain, services (with fakes), adapters (httpx MockTransport),
                    MCP round trips
scripts/            live check against a real Graylog (not in CI)
```

## CI

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs on pushes
to `main` and on manual dispatch:

1. **Lint**: `ruff check` (findings show as GitHub annotations) and
   `ruff format --check`.
2. **Test**: `pytest` on Python 3.11 to 3.15 with 100 % coverage required. The
   suite also checks that the server registers exactly the expected tools. On
   `main`, the coverage badge is published.
3. **Build package** (after lint and test): builds the sdist and wheel and
   uploads them as the `dist` artifact.

[`.github/workflows/trivy.yml`](.github/workflows/trivy.yml) builds the Docker
image and scans it with Trivy for vulnerabilities and secrets. It runs weekly,
on manual dispatch, and on pushes to `main` that touch the `Dockerfile`,
dependencies, `src/` or the scan setup. It fails only on high or critical
findings that already have a fix: the base image and dependencies are pinned,
so a failed weekly run means a fix is out and the pins need a bump. Accepted
findings go in [`.trivyignore.yaml`](.trivyignore.yaml) with a reason and an
expiry date.

[`.github/workflows/codeql.yml`](.github/workflows/codeql.yml) runs CodeQL on
the same triggers and weekly, for the Python code and the workflows. Findings
appear under the repository's Security tab. Dependabot bumps the SHA-pinned
GitHub Actions weekly, once a release is at least 7 days old.

## License

[MIT](LICENSE)
