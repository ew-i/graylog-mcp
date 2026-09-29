# graylog-mcp

Read-only Graylog access exposed as 27 MCP tools over stdio: search, analysis,
alerts, saved searches, cluster health and configuration.

[![build](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml/badge.svg)](https://github.com/ew-i/graylog-mcp/actions/workflows/deploy.yml) [![coverage](https://raw.githubusercontent.com/ew-i/badges/main/graylog-mcp/coverage.svg)](https://github.com/ew-i/graylog-mcp)

## Tools

| Area | Tool | What it does | Needs |
|---|---|---|---|
| Search | `cluster_status` | Version, node, timezone, processing state | |
| | `browse_streams` | Streams you may query, with IDs — call first | |
| | `find_recent_logs` / `find_logs_between` | Message search, relative or absolute window | |
| | `read_log_message` | One message in full | |
| | `rank_field_values` | Top values from a sample of ≤1000 newest matches | |
| | `discover_fields` | Field names seen in recent messages | |
| Analysis | `count_matches` | Number of hits only | |
| | `count_over_time` | Histogram, optionally split by a field | 5.1+ |
| | `exact_field_counts` | Exact top values, counted server-side | 5.1+ |
| | `compare_windows` | Now vs. earlier window; totals and per-value changes | per-value: 5.1+ |
| | `field_stats` | count/avg/min/max/sum/percentiles of a numeric field | 5.1+ |
| | `message_context` | Messages just before/after one message (same source/pod) | |
| Alerts | `recent_alerts` | Alerts/events raised in a window | events read |
| | `alert_definitions` | What each event definition checks and how often | events read |
| Saved views | `saved_searches` / `dashboards` | List them | view read |
| | `run_saved_search` | Run a saved search's query, stream and range | view read |
| Operations | `input_status` | Inputs and their per-node state | inputs read |
| | `throughput` | msg/s in/out, buffer usage, journal backlog | metrics read |
| | `system_notifications` | Graylog's own warnings, urgent first | notifications read |
| | `cluster_nodes` | Nodes, leader, lifecycle, health | cluster read |
| Configuration | `stream_rules` | A stream's routing rules in plain words | stream read |
| | `pipeline_rules` | Pipelines (per stream), stages and rule source | pipeline read |
| | `index_sets` | Rotation/retention, i.e. how far back you can search | index sets read |
| | `lookup_tables` / `lookup_value` | Resolve a key via a lookup table | lookup tables read |

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
    config.py         Settings.from_env
  interface/        MCP tools, one module per area
    mcp_tools.py      ServiceBundle + build_server
    tools_*.py  responses.py
  __main__.py       composition root
tests/              domain, services (with fakes), adapters (MockTransport), MCP round trips
```

## Configuration

| Variable                  | Required | Default |
|---------------------------|----------|---------|
| `GRAYLOG_BASE_URL`        | yes      |         |
| `GRAYLOG_API_TOKEN`       | yes      |         |
| `GRAYLOG_VERIFY_TLS`      | no       | `true`  |
| `GRAYLOG_TIMEOUT_SECONDS` | no       | `30`    |

For self-signed certificates, set `GRAYLOG_VERIFY_TLS=false`.

## Run

### No Docker

```bash
uv sync
uv run graylog-mcp     # or: uv run python -m graylog_mcp
```

### Docker

Build the image from the project root:

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
Optional configuration variables such as `GRAYLOG_VERIFY_TLS` and
`GRAYLOG_TIMEOUT_SECONDS` can be passed with additional `-e` flags.

### Locking Dependencies

The Docker build uses `uv.lock` to install exact dependency versions. Generate
or refresh it from the project root after changing dependencies in
`pyproject.toml`:

```bash
uv lock
```

Verify that the lockfile matches the project metadata with:

```bash
uv lock --check
```

### MCP Client Configuration

The server uses MCP over stdio. Add it to your MCP client configuration, such
as Claude Desktop or Cursor:

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

Replace `/absolute/path/to/graylog-mcp` with the project directory and provide
your Graylog URL and API token. Optional settings can be added under `env`:

```json
{
  "GRAYLOG_VERIFY_TLS": "true",
  "GRAYLOG_TIMEOUT_SECONDS": "30"
}
```

For a self-signed Graylog certificate, set `GRAYLOG_VERIFY_TLS` to `"false"`.

#### OpenCode

For a local installation, add this server to
`~/.config/opencode/opencode.json` (or merge it into a project-level
`opencode.json`):

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

When using the Docker image, configure the MCP client to run Docker with an
interactive stdin. The client must be able to find the image built as
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

Add optional settings as additional Docker arguments, for example:
`"--env", "GRAYLOG_VERIFY_TLS=false"`.

## Develop

```bash
uv sync --dev
uv run pytest                    # or, stdlib only: uv run python -m unittest discover -s tests -t .
uv run ruff check src tests      # PEP 8 + static checks (config in pyproject.toml)
uv run ruff format src tests     # auto-format; CI runs `uv run ruff format --check`
```

Style: PEP 8 via ruff (`E`, `W`, `F`, `N`, `I`, `B`, `UP`, `SIM`, `C4`), 99-character
lines (PEP 8 allows teams to agree on up to 99), `X | None` annotations, exceptions
named `...Error`.

## CI

`.github/workflows/deploy.yml` runs on pushes to `main`, pull requests and manual
dispatch:

1. **lint**: `ruff check` (findings annotate the PR) and `ruff format --check`
2. **test**: `pytest` on Python 3.10–3.14, plus a check that the server wires up all 27 tools
3. **build**: builds the sdist and wheel once lint and tests pass, and uploads them as the `dist` artifact
