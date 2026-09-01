# ptero-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants operate game servers managed by a [Pterodactyl](https://pterodactyl.io) panel: power actions, live console, commands, backups, databases, schedules, activity log. A persistent rolling console buffer keeps "what just happened on the server" one tool call away.

## Overview

Pterodactyl's REST API can start/stop containers and manage resources, but it gives no natural way to read live console output, send a command and see its reply, or react to events as they happen. This server fills that gap: it holds a persistent WebSocket to the Wings daemon per server — auto-reconnect, JWT refresh, rolling line buffer — so reading the recent past or capturing a command's reply is a single tool call. The full Pterodactyl client API (power, backups, databases, schedules, activity log, resources) is exposed as typed MCP tools.

Methodology and composition patterns for the assistant live in [`SKILL.md`](./SKILL.md), picked up automatically by clients that support skills.

## Features

- **Live console** with persistent WebSocket buffering and atomic command/reply semantics.
- **Power**: start / stop / restart / kill, returning timestamps for event correlation.
- **Backups**: list, create, delete, lock, restore, download.
- **Databases**: list, create, rotate password, delete.
- **Schedules + tasks**: full CRUD plus manual execution.
- **Activity log** with actor relationships ("who restarted X at 14:32?").
- Multi-tenant: per-request credentials via headers, bearer-gated, isolated per operator.
- Structured logging, `/healthz`, Docker-ready.

## Quick start

### 1. Prerequisites

- One or more Pterodactyl panels with a Client API key per operator (`Account → API Credentials`)
- Docker + Docker Compose (or Python ≥ 3.13 and [`uv`](https://github.com/astral-sh/uv) for local runs)

### 2. Configure environment

```bash
cp .env.example .env
# set MCP_AUTH_TOKEN (openssl rand -hex 32); tune HTTP_HOST / HTTP_PORT / CONSOLE_* / LOG_*
```

Panel URL and Client API key are **not** in `.env` — clients send them per request (step 4).

### 3. Run with Docker Compose

```bash
docker compose up -d --build
curl http://127.0.0.1:3000/healthz   # {"status":"ok",...}
```

The server listens on `http://0.0.0.0:3000/mcp` by default.

### 4. Connect Claude Code

One MCP entry per (panel, operator key) pair, all pointing at the same server with different headers:

```bash
claude mcp add ptero-prod --transport http http://localhost:3000/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN" \
  --header "X-Pterodactyl-Url: https://panel.prod.example.com" \
  --header "X-Pterodactyl-Key: $PTERO_PROD_KEY"
```

For Claude Desktop or other JSON-config clients:

```json
{
  "mcpServers": {
    "ptero-prod": {
      "type": "http",
      "url": "http://localhost:3000/mcp",
      "headers": {
        "Authorization": "Bearer <your bearer token>",
        "X-Pterodactyl-Url": "https://panel.prod.example.com",
        "X-Pterodactyl-Key": "<your prod client API key>"
      }
    }
  }
}
```

## Multi-panel usage

A single instance serves any number of panels and operator keys. The server holds **no panel URL and no API key** — every `/mcp` request must carry `X-Pterodactyl-Url` and `X-Pterodactyl-Key` (plus the shared `Authorization: Bearer`). Console buffers are isolated per `(panel_url, api_key, server_id)` triple, so two operators never see each other's buffer — enforced via `ContextVar`s set by the auth middleware. Requests missing either header are rejected with HTTP 400.

## Tools

Grouped by domain. See [`SKILL.md`](./SKILL.md) for composition patterns and methodology.

### Discovery & state

| Tool | Description |
|---|---|
| `list_servers` | Paginated list of servers on the panel |
| `get_server` | Full server descriptor (limits, docker image, startup) |
| `get_resources` | Point-in-time CPU / RAM / disk / network / uptime |

### Power

| Tool | Description |
|---|---|
| `power_action` | `start` / `stop` / `restart` / `kill`; returns `sent_at_ms` for correlation |

### Live console

| Tool | Description |
|---|---|
| `tail_console` | Read the rolling buffer (past lines), filterable by `limit` / `since_ms` / `match` |
| `wait_console` | Block until new lines arrive; short-circuit on an `expect` regex |
| `run_command` | Atomically send a command **and** capture its reply window |
| `send_command` | Fire-and-forget command injection; returns `sent_at_ms` |
| `watch_server` / `unwatch_server` | Pin / unpin a session against the idle TTL |
| `list_console_sessions` | Console sessions held by the current credentials |

### Audit

| Tool | Description |
|---|---|
| `get_activity_log` | Paginated audit events with the acting user included |

### Backups

`list_backups`, `get_backup`, `create_backup`, `delete_backup`, `toggle_backup_lock`, `restore_backup`, `get_backup_download_url`

### Databases

`list_databases`, `create_database`, `rotate_database_password`, `delete_database`

### Schedules

`list_schedules`, `get_schedule`, `create_schedule`, `update_schedule`, `delete_schedule`, `execute_schedule`, `create_schedule_task`, `update_schedule_task`, `delete_schedule_task`

### Field selection

Read tools return the full upstream payload — Pterodactyl's JSON:API has no server-side field selection, and most callers use the full descriptor. Narrow at the call site if a specific payload becomes a problem.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `MCP_AUTH_TOKEN` | yes | — | Bearer token required on every request. Generate with `openssl rand -hex 32` |
| `HTTP_HOST` | no | `0.0.0.0` | Bind host |
| `HTTP_PORT` | no | `3000` | Bind port |
| `CONSOLE_BUFFER_SIZE` | no | `5000` | Rolling buffer size per session. Bump on chatty servers |
| `CONSOLE_IDLE_TTL` | no | `600` | Seconds of inactivity before an unpinned session is closed |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_JSON` | no | `false` | Emit logs as JSON (recommended in production) |

Panel URL and Client API key are supplied per request via the `X-Pterodactyl-Url` / `X-Pterodactyl-Key` headers, never as env vars. The `docker-compose.yml` also honors `HTTP_BIND` for the host side of the port mapping — set it to a Tailscale IP or `127.0.0.1` to avoid public exposure.

## Logging

`structlog`; human-readable by default, JSON via `LOG_JSON=true`. Panel URLs and API keys are never logged.

## Development

```bash
uv sync
uv run ptero-mcp                 # run the server
uv run ruff check src            # lint
uv run mypy src                  # type-check (strict)
```

## Deployment notes

- **Published image.** Tagging a release (`git tag v1.2.3 && git push --tags`) builds and pushes `ghcr.io/mediavee/ptero-mcp-server:1.2.3` + `:latest`. To run from the registry, uncomment the `image:` line in `docker-compose.yml`, then `docker compose pull && docker compose up -d`.
- **Security.** Credentials travel in headers; never logged or persisted. TLS in front is mandatory unless bound to loopback / Tailscale / WireGuard. `MCP_AUTH_TOKEN` gates access. Keep `.env` git-ignored.
- **Panel on the same host.** When a panel resolves to the Docker host's own private
  address (Tailscale, WireGuard), a bridged container cannot reach it: the server
  answers panels that live elsewhere and times out on that one. Run with
  `network_mode: host` and bind `HTTP_HOST` to the private address.
- **Health check.** Probe the address the server actually binds. With `HTTP_HOST` set to
  a private address, a `127.0.0.1` probe reports the container unhealthy while it serves
  every request normally.
- **Pin the image name when building.** Compose derives the image tag from the directory
  name, so renaming the deployment directory silently reuses whatever image already
  carries the new derived name. Set an explicit `image:` alongside `build:`.
- **Graceful shutdown.** `SIGTERM` / `SIGINT` closes all sessions and transports, then exits (10s uvicorn cap).

## License

[MIT](./LICENSE) © Mediavee.
