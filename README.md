# graylog-mcp

Read-only Graylog access exposed as 30 MCP tools, 8 MCP resources, one resource
template and 8 workflow prompts over stdio or Streamable HTTP: search, analysis,
alerts, saved searches, cluster health and configuration.

[![build](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml) [![coverage](https://raw.githubusercontent.com/ew-i/badges/main/graylog-mcp/coverage.svg)](https://github.com/ew-i/graylog-mcp) [![codeql](https://github.com/ew-i/graylog-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/codeql.yml) ![Python](https://img.shields.io/badge/python-3.11_%E2%80%93_3.15-yellow?labelColor=blue) ![Graylog](https://img.shields.io/badge/graylog-5.1_%E2%80%93_7.2-08c2fd)

## Tools

| Area          | Tool                                     | What it does                                                            | Needs              |
|---------------|------------------------------------------|-------------------------------------------------------------------------|--------------------|
| Search        | `cluster_status`                         | Version, node, timezone, processing state                               |                    |
|               | `server_capabilities`                    | Version, optional APIs it offers, tools it cannot serve                 |                    |
|               | `browse_streams`                         | Streams you may query, with IDs — call first                            |                    |
|               | `find_recent_logs` / `find_logs_between` | Message search; each hit carries `_index` and `_id`                     |                    |
|               | `read_log_message`                       | One message in full                                                     |                    |
|               | `rank_field_values`                      | Top values from a sample of ≤1000 newest matches                        |                    |
|               | `discover_fields`                        | Fields with type, and whether groupable or numeric                      |                    |
|               | `validate_query`                         | Syntax and unknown-field check, without searching                       |                    |
| Analysis      | `count_matches`                          | Number of hits only                                                     |                    |
|               | `count_over_time`                        | Histogram, optionally split by a field                                  | 6.3+ (not 5.1)     |
|               | `exact_field_counts`                     | Exact top values, counted server-side                                   | 5.1+               |
|               | `compare_windows`                        | Now vs. earlier window; totals and per-value changes                    | per-value: 5.1+    |
|               | `field_stats`                            | count/avg/min/max/sum/percentiles of a numeric field                    | 5.1+               |
|               | `message_context`                        | Messages just before/after one message (same source/pod)                |                    |
| Alerts        | `recent_alerts`                          | Alerts/events raised in a window                                        | events read        |
|               | `alert_definitions`                      | What each event definition checks and how often                         | events read        |
| Saved views   | `saved_searches` / `dashboards`          | List them                                                               | view read          |
|               | `run_saved_search`                       | Run a saved search's query, stream and range                            | view read          |
| Operations    | `input_status`                           | Inputs and their per-node state                                         | inputs read        |
|               | `throughput`                             | msg/s in/out, buffer usage, journal backlog                             | metrics read       |
|               | `system_notifications`                   | Graylog's own warnings, urgent first                                    | notifications read |
|               | `cluster_nodes`                          | Nodes, leader, lifecycle, health                                        | cluster read       |
| Configuration | `stream_rules`                           | A stream's routing rules in plain words                                 | stream read        |
|               | `pipeline_rules`                         | Pipelines (per stream), stages and rule source                          | pipeline read      |
|               | `index_sets`                             | Rotation/retention, i.e. how far back you can search                    | index sets read    |
|               | `lookup_tables` / `lookup_value`         | Resolve a key via a lookup table                                        | lookup tables read |
| Investigation | `investigate_incident`                   | Composite incident triage across health, logs, alerts and configuration | varies             |

**5.1+** tools use Graylog's Search Scripting API. On older servers they return
`{"kind": "Unsupported", ...}`; the rest keep working. `count_over_time` also needs
time grouping, which the 5.1 API lacks; it is verified on 6.3 and 7.2 (5.2 to 6.2
are untested). `server_capabilities` checks each optional API on the connected
server (one request each, none runs a search) and lists the tools that return
`Unsupported` there. Once an API is known to be missing, tools that need it answer
`Unsupported` without asking Graylog again, for as long as resources are cached. **Needs** lists the token
permissions beyond reading streams; without them a tool returns `{"kind": "AccessDenied"}`.

All tools are read-only. Paginated logs, events, streams, dashboards, saved
searches, alert definitions, and lookup tables return `limit`, `next_cursor`,
`has_more`, and `truncated`; pass `next_cursor` to fetch the next page. The
cursor carries the original request, so later pages repeat it exactly; a
relative time range is pinned to absolute timestamps on the first page, so
paging never shifts while new messages arrive. `truncated` (with a
`truncation` explanation) means results are missing that paging alone will not
recover as asked: the requested limit exceeded the maximum page size, or the
matches go deeper than Graylog can page (10,000 for logs and events).
Failures never raise: they return `{"error": ..., "kind": ...}`. An unexpected
internal error has kind `Internal` and is logged with its traceback to stderr;
in `investigate_incident` it replaces only the failing section.

## Resources

Stable reference data is also exposed as MCP resources, so a client can attach
it as context without a tool call. The Graylog-backed resources return the same
JSON as the matching tool (redacted; failures as `{"error": ..., "kind": ...}`).

| Resource                               | Contents                                           | Same as               |
|----------------------------------------|----------------------------------------------------|-----------------------|
| `graylog://streams`                    | Streams you may query, with IDs                    | `browse_streams`      |
| `graylog://saved-searches`             | Saved searches, with IDs for `run_saved_search`    | `saved_searches`      |
| `graylog://streams/{stream_id}/fields` | A stream's fields with type and capabilities (30d) | `discover_fields`     |
| `graylog://cluster/status`             | Version, cluster identity, processing state        | `cluster_status`      |
| `graylog://cluster/capabilities`       | Optional APIs offered, tools this server can't run | `server_capabilities` |
| `graylog://alerts/definitions`         | Event/alert definitions                            | `alert_definitions`   |
| `graylog://config/index-sets`          | Rotation and retention                             | `index_sets`          |
| `graylog://config/pipelines`           | Pipelines, stages and rules                        | `pipeline_rules`      |
| `graylog://guide/query-syntax`         | Query syntax and how to use these tools            | static Markdown       |

`graylog://streams/{stream_id}/fields` is a resource template; clients list it
separately from the fixed resources. Successful Graylog reads are cached per
URI (60 seconds by default; set `MCP_RESOURCE_CACHE_SECONDS` or
`--resource-cache-seconds`, `0` disables), so repeated reads do not refetch;
failures are not cached. Catalogue resources return the first 200 entries with
the usual `has_more`/`next_cursor`; page further with the tool. Tools are never
cached; use them when the data must be current, such as cluster health. The same
setting bounds how long the server remembers which optional Graylog APIs exist.

## Prompts

Prompts are workflow templates a user picks in the client (often as a slash
command). Each renders step-by-step instructions naming the tools to call and
what to report; prompts themselves never call Graylog.

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

Without a `stream_id`, a prompt tells the model to pick one with `browse_streams`;
`explain-log-message` reads it from the message instead.

## Layout

```
src/graylog_mcp/
  domain/           pure models, invariants, analysis — no I/O, no frameworks
    models.py         LogQuery, time windows, LogEntry, Stream, ClusterInfo
    capabilities.py   optional Graylog APIs (Capability)
    aggregation.py    AggregationQuery, groupings, metrics, intervals
    alerts.py  views.py  operations.py  configuration.py  timeutil.py
    analysis.py       sampled ranking, field discovery
    errors.py         error hierarchy used across all layers
  application/      use cases + policy; depends only on domain and ports
    ports.py          StreamCatalog + MessageStore (= LogStore), AggregationStore,
                      CapabilityStore, AlertStore, ViewStore, SystemStore, ConfigStore
    policy.py         Limits, clamping, argument parsing
    service.py        LogService (search)
    analytics.py  alerts.py  saved_views.py  operations.py  configuration.py
    capabilities.py   CapabilityService (server_capabilities)
  infrastructure/   Graylog REST adapters, one per port
    graylog_api.py    shared HTTP client: auth, error mapping, JSON
    graylog_capabilities.py  probes and remembers which optional APIs exist
    graylog_http.py  graylog_analytics.py  graylog_alerts.py
    graylog_views.py  graylog_system.py  graylog_config.py
    config.py         Pydantic Settings loaded from the environment
  interface/        MCP tools, one module per area, and resources
    mcp_tools.py      ServiceBundle + build_server
    resources.py      graylog:// resources with a short-lived cache
    query_guide.py    text of the graylog://guide/query-syntax resource
    prompts.py        workflow prompts
    tools_*.py  responses.py
  cli.py             argparse entrypoint and composition root
tests/              domain, services (with fakes), adapters (MockTransport), MCP round trips
```

## Configuration

| Variable                              | Required | Default                   |
|---------------------------------------|----------|---------------------------|
| `GRAYLOG_BASE_URL`                    | yes      |                           |
| `GRAYLOG_API_TOKEN`                   | yes      |                           |
| `GRAYLOG_VERIFY_TLS`                  | no       | `true`                    |
| `GRAYLOG_ALLOW_INSECURE_HTTP`         | no       | `false`                   |
| `GRAYLOG_TIMEOUT_SECONDS`             | no       | `30`                      |
| `MCP_REDACT_FIELDS`                   | no       | built-in sensitive fields |
| `MCP_LOG_LEVEL`                       | no       | `INFO`                    |
| `MCP_TRANSPORT`                       | no       | `stdio`                   |
| `MCP_HOST`                            | no       | `127.0.0.1`               |
| `MCP_PORT`                            | no       | `8000`                    |
| `MCP_STREAMABLE_HTTP_PATH`            | no       | `/mcp`                    |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | no       | `false`                   |
| `MCP_ALLOWED_HOSTS`                   | no       | empty                     |
| `MCP_ALLOWED_ORIGINS`                 | no       | empty                     |
| `MCP_RESOURCE_CACHE_SECONDS`          | no       | `60`                      |

MCP responses redact credentials before they leave the server:

- Values under sensitive key names, such as any key containing `password`,
  `secret`, `token` or `api_key`, or ending in `auth`, `session` or `cookie`.
  Keys that only describe a secret (`token_count`, `token_type`) and
  true/false or null values are kept.
- Credential patterns in text: labelled values (`password=…`,
  `"token": "…"`, `Authorization: …`), passwords in URLs of any scheme,
  bearer tokens, JWTs, private keys, webhook URLs, Azure keys and SAS
  signatures, and well-known token formats (AWS, GitHub, GitLab, Slack,
  Google, Stripe, OpenAI/Anthropic, npm, PyPI).
- The configured `GRAYLOG_API_TOKEN`, wherever its exact value appears.
- Values of a sensitive field that a tool reports on, such as
  `rank_field_values` on `session_id`, become numbered placeholders
  (`[REDACTED-1]`), so counts and repeats stay visible.

Add application-specific field names as a comma-separated list with
`MCP_REDACT_FIELDS`; they match key names exactly, ignoring case and
punctuation. Built-in redaction rules cannot be disabled. Redaction does not
stop a client from narrowing a secret with repeated searches such as
`password:a*`; restrict the token's stream permissions for that.

`MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` are comma-separated. When
DNS-rebinding protection is enabled, every HTTP client must use an allowed
`Host` value and, when present, an allowed `Origin` value. The values support
the MCP SDK's `:*` port wildcard, such as `localhost:*` or
`https://app.example:*`.

**Graylog connection options**

| Option                                               | Description                                          |
|------------------------------------------------------|------------------------------------------------------|
| `--base-url`                                         | Graylog URL                                          |
| `--api-token`                                        | Graylog API token                                    |
| `--verify-tls` / `--no-verify-tls`                   | Verify the Graylog TLS certificate                   |
| `--allow-insecure-http` / `--no-allow-insecure-http` | Allow an `http://` Graylog URL for local development |
| `--timeout-seconds`                                  | HTTP request timeout                                 |

**MCP server options**

| Option                                                                       | Description                                                       |
|------------------------------------------------------------------------------|-------------------------------------------------------------------|
| `--log-level`                                                                | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `--transport`                                                                | MCP transport: `stdio` or `streamable-http`                       |
| `--host`                                                                     | HTTP listen host                                                  |
| `--port`                                                                     | HTTP listen port                                                  |
| `--streamable-http-path`                                                     | Streamable HTTP endpoint path                                     |
| `--enable-dns-rebinding-protection` / `--no-enable-dns-rebinding-protection` | Validate HTTP `Host` and `Origin` headers                         |
| `--allowed-hosts`                                                            | Comma-separated allowed HTTP `Host` values                        |
| `--allowed-origins`                                                          | Comma-separated allowed HTTP `Origin` values                      |
| `--redact-fields`                                                            | Comma-separated extra field names whose values are redacted       |
| `--resource-cache-seconds`                                                   | Seconds MCP resource reads are cached; `0` disables caching       |

Settings are also read from `.env`. Precedence is CLI
arguments, process environment variables, `.env`, then the declared defaults.
A CLI argument replaces its environment value entirely, so an invalid
environment value does not block startup when the matching flag is given.

## Run

### No Docker

Create a local environment file from the example and edit the required values:

```bash
cp .env.example .env
uv sync
uv run graylog-mcp     # or: uv run python -m graylog_mcp.cli
```

CLI arguments can override values from `.env` and the process environment. For
example: `uv run graylog-mcp --log-level DEBUG --timeout-seconds 60`.

### Streamable HTTP

Run a Streamable HTTP MCP endpoint with the environment or equivalent CLI
arguments:

```bash
MCP_TRANSPORT=streamable-http \
MCP_HOST=127.0.0.1 \
MCP_PORT=8000 \
uv run graylog-mcp
```

The endpoint is available at `http://127.0.0.1:8000/mcp` by default. For a
publicly reachable deployment, enable DNS-rebinding protection and list the
hostnames and browser origins that clients are allowed to send:

```bash
MCP_TRANSPORT=streamable-http \
MCP_HOST=0.0.0.0 \
MCP_ENABLE_DNS_REBINDING_PROTECTION=true \
MCP_ALLOWED_HOSTS=mcp.example.com:8000 \
MCP_ALLOWED_ORIGINS=https://app.example \
uv run graylog-mcp
```

### Docker

The image supports both stdio and Streamable HTTP. Build it from the
project root:

```bash
docker build -t graylog-mcp .
```

Run the MCP server over stdio:

```bash
docker run --rm -i \
  -e GRAYLOG_BASE_URL=https://graylog.example.com \
  -e GRAYLOG_API_TOKEN=your-graylog-api-token \
  graylog-mcp
```

The `-i` flag keeps standard input open because MCP communicates over stdio.

To run Streamable HTTP instead, publish port `8000` and select the transport
with MCP environment variables:

```bash
docker run --rm \
  -p 8000:8000 \
  -e GRAYLOG_BASE_URL=https://graylog.example.com \
  -e GRAYLOG_API_TOKEN=your-graylog-api-token \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  -e MCP_PORT=8000 \
  graylog-mcp
```

The endpoint is then available at `http://localhost:8000/mcp`. For a deployed
endpoint, configure `MCP_ENABLE_DNS_REBINDING_PROTECTION`,
`MCP_ALLOWED_HOSTS`, and `MCP_ALLOWED_ORIGINS` as described above.

### MCP Client Configuration

For stdio, add it to your MCP client configuration, such as Claude Desktop or
Cursor:

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

Replace `/absolute/path/to/graylog-mcp` with the project directory.

For Streamable HTTP, start the server with `MCP_TRANSPORT=streamable-http`
and configure the client with the endpoint URL. For example, clients that use
the standard remote MCP shape can use:

```json
{
  "mcpServers": {
    "graylog": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

#### OpenCode

For a local stdio installation, add this server to
`~/.config/opencode/opencode.json` (or merge it into a project-level `opencode.json`):

```json
{
  "mcp": {
    "graylog": {
      "type": "local",
      "command": [
        "/absolute/path/to/graylog-mcp/.venv/bin/graylog-mcp"
      ],
      "environment": {
        "GRAYLOG_BASE_URL": "https://graylog.example.com",
        "GRAYLOG_API_TOKEN": "your-graylog-api-token"
      },
      "enabled": true
    }
  }
}
```

### Docker Client Configuration

When using the Docker image over stdio, configure the MCP client to run Docker
with an interactive stdin. The client must be able to find the image built as
`graylog-mcp`.

#### Claude Desktop

Add this server to Claude Desktop's MCP configuration:

```json
{
  "mcpServers": {
    "graylog": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "GRAYLOG_BASE_URL=https://graylog.example.com",
        "-e",
        "GRAYLOG_API_TOKEN=your-graylog-api-token",
        "graylog-mcp"
      ]
    }
  }
}
```

#### OpenCode

Add this server to your OpenCode configuration at `~/.config/opencode/opencode.json`
(or merge it into an existing project-level `opencode.json`):

```json
{
  "mcp": {
    "graylog": {
      "type": "local",
      "command": [
        "docker",
        "run",
        "--rm",
        "-i",
        "--env",
        "GRAYLOG_BASE_URL=https://graylog.example.com",
        "--env",
        "GRAYLOG_API_TOKEN=your-graylog-api-token",
        "graylog-mcp"
      ],
      "enabled": true
    }
  }
}
```

## Develop

```bash
uv sync --dev
uv run pytest                    # or, stdlib only: uv run python -m unittest discover -s tests -t .
uv run ruff check src tests      # PEP 8 + static checks (config in pyproject.toml)
uv run ruff format src tests     # auto-format; CI runs `uv run ruff format --check`
uv lock                          # lock to exact dependency versions
uv lock --check                  # verify that the lockfile matches the project metadata
```

Style: PEP 8 via ruff (`E`, `W`, `F`, `N`, `I`, `B`, `UP`, `SIM`, `C4`), 99-character
lines (PEP 8 allows teams to agree on up to 99), `X | None` annotations, exceptions
named `...Error`.

**Live check** (opt-in, not in CI): runs every tool, resource and prompt against a
real Graylog. `scripts/live/stack.py` starts a throwaway stack (Graylog, OpenSearch,
MongoDB) in Docker for each supported version line, on localhost only with the
login admin / admin:

| Version | Web / API                | GELF HTTP                     |
|---------|--------------------------|-------------------------------|
| `5.1`   | `http://localhost:29000` | `http://localhost:29001/gelf` |
| `6.3`   | `http://localhost:39000` | `http://localhost:39001/gelf` |
| `7.2`   | `http://localhost:49000` | `http://localhost:49001/gelf` |

```bash
python scripts/live/stack.py up 7.2
uv run python scripts/live_check.py --base-url http://localhost:49000 \
    --user admin --password admin --gelf-url http://localhost:49001/gelf
python scripts/live/stack.py down 7.2
```

Against your own server, pass `--api-token`, your GELF HTTP input URL and the
`--stream-id` its messages land in.

### Coding agents

The repo enables two agent add-ons: [ponytail](https://github.com/DietrichGebert/ponytail)
(simplest-solution mode) and [graphify](https://pypi.org/project/graphifyy/) (code knowledge
graph). Once per machine:

```bash
uv tool install graphifyy                                  # the .claude hooks call `graphify`
claude plugin install ponytail@ponytail --scope project    # run in the repo root
```

- ponytail's hooks run `node`, so Node.js must be on your `PATH`.

## CI

`.github/workflows/deploy.yml` runs on pushes to `main` and manual dispatch:

1. **lint**: `ruff check` (findings annotate the PR) and `ruff format --check`
2. **test**: `pytest` on Python 3.11–3.15, plus a check that the server wires up all 29 tools
3. **build**: builds the sdist and wheel once lint and tests pass, and uploads them as the `dist` artifact

`.github/workflows/codeql.yml` runs CodeQL on the same triggers and weekly, for the
Python code and the workflows themselves; findings appear under the repository's
Security tab (code scanning).
