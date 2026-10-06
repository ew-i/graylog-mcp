# graylog-mcp

Read-only Graylog access exposed as 28 MCP tools over stdio or Streamable HTTP:
search, analysis, alerts, saved searches, cluster health and configuration.

[![build](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml) [![coverage](https://raw.githubusercontent.com/ew-i/badges/main/graylog-mcp/coverage.svg)](https://github.com/ew-i/graylog-mcp)

## Tools

| Area          | Tool                                     | What it does                                                            | Needs              |
|---------------|------------------------------------------|-------------------------------------------------------------------------|--------------------|
| Search        | `cluster_status`                         | Version, node, timezone, processing state                               |                    |
|               | `browse_streams`                         | Streams you may query, with IDs — call first                            |                    |
|               | `find_recent_logs` / `find_logs_between` | Message search, relative or absolute window                             |                    |
|               | `read_log_message`                       | One message in full                                                     |                    |
|               | `rank_field_values`                      | Top values from a sample of ≤1000 newest matches                        |                    |
|               | `discover_fields`                        | Field names seen in recent messages                                     |                    |
| Analysis      | `count_matches`                          | Number of hits only                                                     |                    |
|               | `count_over_time`                        | Histogram, optionally split by a field                                  | 5.1+               |
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
`{"kind": "Unsupported", ...}`; the rest keep working. **Needs** lists the token
permissions beyond reading streams; without them a tool returns `{"kind": "AccessDenied"}`.

All tools are read-only. Failures never raise: they return `{"error": ..., "kind": ...}`.

## Layout

```
src/graylog_mcp/
  domain/           pure models, invariants, analysis — no I/O, no frameworks
    models.py         LogQuery, time windows, LogEntry, Stream, ClusterInfo
    aggregation.py    AggregationQuery, groupings, metrics, intervals
    alerts.py  views.py  operations.py  configuration.py  timeutil.py
    analysis.py       sampled ranking, field discovery
    errors.py         error hierarchy used across all layers
  application/      use cases + policy; depends only on domain and ports
    ports.py          LogStore, AggregationStore, AlertStore, ViewStore, SystemStore, ConfigStore
    policy.py         Limits, clamping, argument parsing
    service.py        LogService (search)
    analytics.py  alerts.py  saved_views.py  operations.py  configuration.py
  infrastructure/   Graylog REST adapters, one per port
    graylog_api.py    shared HTTP client: auth, error mapping, JSON
    graylog_http.py  graylog_analytics.py  graylog_alerts.py
    graylog_views.py  graylog_system.py  graylog_config.py
    config.py         Pydantic Settings loaded from the environment
  interface/        MCP tools, one module per area
    mcp_tools.py      ServiceBundle + build_server
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

MCP responses redact built-in credential fields and common credential patterns.
Add application-specific field names as a comma-separated list with
`MCP_REDACT_FIELDS`; built-in redaction rules cannot be disabled.

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

Settings are also read from `.env`. Precedence is CLI
arguments, process environment variables, `.env`, then the declared defaults.

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

## CI

`.github/workflows/deploy.yml` runs on pushes to `main`, pull requests and manual
dispatch:

1. **lint**: `ruff check` (findings annotate the PR) and `ruff format --check`
2. **test**: `pytest` on Python 3.10–3.14, plus a check that the server wires up all 28 tools
3. **build**: builds the sdist and wheel once lint and tests pass, and uploads them as the `dist` artifact
