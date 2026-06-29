# ptero-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants operate game servers managed by a [Pterodactyl](https://pterodactyl.io) panel. Power actions, live console, commands, backups, schedules, activity log — with a persistent rolling buffer so "what just happened on the server" is always one tool call away.

Built on **[FastMCP 3.x](https://gofastmcp.com)** + Python 3.13 + asyncio. The architecture is intentionally portable: the same skeleton (settings → client → tool registration → custom routes) can host any other Pterodactyl-style integration with minimal churn.

---

## Overview

Pterodactyl's REST API is enough to start/stop containers and manage resources, but it does **not** give you a natural way to read live console output, send a command and see its reply, or react to events as they happen. This server fills that gap:

- It keeps a **persistent WebSocket** open to the Wings daemon for every server you touch, with automatic reconnect and JWT refresh every 8 minutes.
- Each session has a **rolling line buffer** (default 5000 lines, O(1) `deque`), so `tail_console` returns the recent past instantly — no polling, no missed output.
- A dedicated `run_command` tool **atomically sends a command and captures its reply**, with an optional regex short-circuit. No race on chatty consoles.
- A `wait_console` tool blocks the current tool call until a matching line arrives — ideal for "restart and tell me when it's back".

The full Pterodactyl client API surface — power, backups, databases, schedules, activity log, resources — is also exposed as typed MCP tools.

## Features

- **Live console** with persistent WebSocket buffering and atomic command/reply semantics
- **Power management** (start/stop/restart/kill) returning timestamps for precise event correlation
- **Backups**: list, create, delete, lock, restore, download
- **Databases**: list, create, rotate password, delete
- **Schedules + tasks**: full CRUD plus `execute_schedule` for manual triggers
- **Activity log** with actor relationships included (answer "who restarted X at 14:32?")
- **Single process, multi-server**: handles any number of Pterodactyl servers concurrently
- **Bearer-token authenticated** HTTP transport, suitable for private-network or VPN-fronted deployments
- **Structured JSON-capable logging**, retry-aware HTTP client (3 attempts on 5xx with exponential backoff)

## Quick start

### 1. Prerequisites

- One or more Pterodactyl panels with a Client API key per operator (`Account → API Credentials`)
- Python **3.13+** **or** Docker
- (Dev) [`uv`](https://github.com/astral-sh/uv) for dependency management

### 2. Configure environment

```bash
cp .env.example .env
```

Only `MCP_AUTH_TOKEN` is required server-side. The panel URL and Client API key are **not** stored on the server — each MCP client passes them on every request via `X-Pterodactyl-Url` and `X-Pterodactyl-Key` headers (see Multi-panel usage below).

```env
MCP_AUTH_TOKEN=$(openssl rand -hex 32)
```

### 3. Run with Docker Compose

```bash
docker compose up -d --build
```

The server listens on `http://0.0.0.0:3000/mcp` by default. A `/healthz` endpoint is exposed for container health checks.

### 4. Connect Claude Code

One MCP entry per (panel, operator key) pair, all pointing at the same server with different headers:

```bash
claude mcp add ptero-prod --transport http http://localhost:3000/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN" \
  --header "X-Pterodactyl-Url: https://panel.prod.example.com" \
  --header "X-Pterodactyl-Key: $PTERO_PROD_KEY"

claude mcp add ptero-staging --transport http http://localhost:3000/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN" \
  --header "X-Pterodactyl-Url: https://panel.staging.example.com" \
  --header "X-Pterodactyl-Key: $PTERO_STAGING_KEY"
```

For Claude Desktop or other clients that use JSON config:

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

Once connected, the skill file [`SKILL.md`](./SKILL.md) at the repo root is picked up automatically by clients that support it, giving the assistant concrete methodology for common flows (restart, diagnose, send command, etc.).

## Multi-panel usage

A single `ptero-mcp` instance serves any number of Pterodactyl panels and any number of operator keys. The server holds **no panel URL and no API key** in its config — every `/mcp` request must include `X-Pterodactyl-Url` and `X-Pterodactyl-Key` headers, and the server uses them for the upstream Pterodactyl call.

- Per-request credentials live in your MCP client config (Claude Code/Desktop), one entry per (panel, key) pair.
- Console buffers are isolated per `(panel_url, api_key, server_id)` triple — two operators with different keys never see each other's buffer for the same server, even on the same panel. This is enforced via Python `ContextVar`s set by the auth middleware.
- `list_console_sessions` only returns sessions belonging to the current request's credentials.
- Requests missing either header are rejected with HTTP 400 before reaching FastMCP.

## Tools

Thirty-two MCP tools grouped into seven categories. See [`SKILL.md`](./SKILL.md) for composition patterns and methodology.

### Discovery & state

| Tool | Description |
|---|---|
| `list_servers` | Paginated list of servers on the panel |
| `get_server` | Full server descriptor (limits, docker image, startup) |
| `get_resources` | Point-in-time CPU / RAM / disk / network / uptime |

### Power

| Tool | Description |
|---|---|
| `power_action` | Send `start` / `stop` / `restart` / `kill`. Returns `sent_at_ms` for correlation with `wait_console` |

### Live console

| Tool | Description |
|---|---|
| `tail_console` | Read the rolling buffer (past lines). Filterable by `limit`, `since_ms`, or `match` regex |
| `wait_console` | Block until new lines arrive; short-circuit on an `expect` regex |
| `run_command` | Atomically send a command **and** capture its response window. Optional `expect` regex |
| `send_command` | Fire-and-forget command injection. Returns `sent_at_ms` |
| `watch_server` / `unwatch_server` | Pin or unpin a session against the idle TTL |
| `list_console_sessions` | Introspection of currently held sessions and their subscriber counts |

### Audit

| Tool | Description |
|---|---|
| `get_activity_log` | Paginated audit events with actor relationship included |

### Backups

`list_backups`, `get_backup`, `create_backup`, `delete_backup`, `toggle_backup_lock`, `restore_backup`, `get_backup_download_url`

### Databases

`list_databases`, `create_database`, `rotate_database_password`, `delete_database`

### Schedules

`list_schedules`, `get_schedule`, `create_schedule`, `update_schedule`, `delete_schedule`, `execute_schedule`, `create_schedule_task`, `update_schedule_task`, `delete_schedule_task`

### Field selection

The Pterodactyl JSON:API does not support server-side field selection, so read tools return the full upstream payload. Client-side filtering is intentionally not implemented — the upstream wire cost is unchanged, and most callers genuinely use the full server descriptor (limits, container, status). If a specific tool's payload becomes a problem in practice, narrow it at the call site.

## Console architecture

The console subsystem is what makes this server worth its own repo.

### The problem

Pterodactyl's `POST /servers/:id/command` is fire-and-forget: it does not return the command's output. The only way to observe output is to connect to Wings over a WebSocket and read the stream. On a busy server (populated survival, verbose plugins), hundreds of unrelated lines can arrive per second, making it nearly impossible to correlate "this line is the reply to the command I just sent" without careful timing.

### How it works

`ConsoleHub` maintains one lazy-initialized WebSocket per server, with automatic reconnect, JWT refresh every 8 minutes, and a rolling ring buffer (`collections.deque`). On top of that, three read patterns are exposed:

| Pattern | Tool | Mechanics |
|---|---|---|
| **Past** | `tail_console` | Returns lines already in the buffer, optionally filtered |
| **Future (sync)** | `wait_console` | Registers a transient listener, blocks up to `wait_ms`, short-circuits on `expect` regex. Optional `since_ms` also scans recently buffered lines — atomic, no race |
| **Command + reply** | `run_command` | Registers listener **before** sending the command, collects lines for a bounded window **after** the panel accepts it. `expect` regex short-circuits as soon as the reply appears |

The listener API is built on a single shared coroutine (`_capture_with_listener`) so adding new read patterns is cheap.

### Mental model

| Intent | Use |
|---|---|
| Read what happened recently | `tail_console` |
| Wait for one specific event in the current turn | `wait_console` |
| Send a command and capture its reply | `run_command` |
| Send a command and ignore the output | `send_command` |

## Configuration

All configuration is via environment variables (loaded from process env, then `.env`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `MCP_AUTH_TOKEN` | yes | — | Bearer token required by every client. Generate with `openssl rand -hex 32` |
| `HTTP_HOST` | no | `0.0.0.0` | Bind host for the HTTP listener |
| `HTTP_PORT` | no | `3000` | Bind port |
| `CONSOLE_BUFFER_SIZE` | no | `5000` | Rolling buffer size per (panel, key, server) session. Bump on chatty servers where 5000 lines covers only a few seconds |
| `CONSOLE_IDLE_TTL` | no | `600` | Seconds of inactivity before an unpinned, unwatched console session is closed |
| `LOG_LEVEL` | no | `INFO` | Logger level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_JSON` | no | `false` | Emit logs as JSON (recommended for production) |

The panel URL and Client API key are **not** env vars — they are supplied per-request by the MCP client through the `X-Pterodactyl-Url` and `X-Pterodactyl-Key` headers (see Multi-panel usage above).

The included `docker-compose.yml` also honors a `HTTP_BIND` variable for the *host-side* of the port mapping — set it to a Tailscale IP or `127.0.0.1` to avoid exposing the server on public interfaces.

## Development

Use `uv` for the local toolchain:

```bash
uv sync                          # create .venv + install deps
uv run ptero-mcp                 # run the server
uv run python -m ptero_mcp       # equivalent
uv run ruff check src            # lint
uv run ruff format src           # format
uv run mypy src                  # type-check (strict)
```

The codebase is small, async-first, and flat:

```
src/ptero_mcp/
├── __main__.py        # python -m ptero_mcp / console script entry
├── server.py          # FastMCP app + lifespan + ASGI wiring + uvicorn
├── config.py          # pydantic-settings: typed env loading
├── logging.py         # structlog config (text or JSON)
├── auth.py            # Bearer token middleware (constant-time compare)
├── client.py          # httpx-based PterodactylClient (retry, backoff)
├── console_hub.py     # Persistent WS hub, ring buffer, listener plumbing
├── context.py         # Shared dependency container (ToolContext)
└── tools/
    ├── __init__.py    # register_all(mcp, ctx)
    ├── _common.py     # Shared error mapping + helpers
    ├── servers.py     # list_servers, get_server, get_resources
    ├── power.py       # power_action
    ├── console.py     # tail / wait / run / send / watch / list sessions
    ├── activity.py    # get_activity_log
    ├── backups.py
    ├── databases.py
    └── schedules.py
```

## Reusing this skeleton for other MCPs

The wiring is intentionally generic. To start a new MCP service from this template:

1. Replace `client.py` with your upstream API wrapper (httpx async, retry-aware).
2. Replace `console_hub.py` with whatever stateful background subsystem you need (or delete it).
3. Add a tool module per logical domain in `tools/` and register it in `tools/__init__.py`.
4. Update `Settings` in `config.py` with the env vars you need; `pydantic-settings` validates them at startup.
5. Add custom HTTP routes in `server.py` next to `/healthz`.

Everything else — auth middleware, FastMCP lifespan, structured logging, Docker, healthcheck — is reusable as-is.

## Deployment notes

- **Security.** Panel URLs and Client API keys travel in headers (`X-Pterodactyl-Url`, `X-Pterodactyl-Key`). The server does not log them and does not persist them. **TLS in front is non-negotiable** unless the listener is bound to loopback or a private network (Tailscale, WireGuard). `MCP_AUTH_TOKEN` gates access at the bearer layer. `.env` is in `.gitignore` — keep it that way.
- **Multi-panel, multi-operator.** A single process handles any number of panels and any number of operator keys concurrently. Console sessions are keyed by `(panel_url, api_key, server_id)` for strict isolation — different keys never share a buffer, even on the same panel. Sessions are reaped after `CONSOLE_IDLE_TTL` seconds of inactivity unless pinned (`watch_server`).
- **Graceful shutdown.** `SIGTERM` / `SIGINT` closes all sessions and transports, then exits. Uvicorn's 10-second graceful-shutdown timeout forces exit if anything hangs.

## License

[MIT](./LICENSE) © Mediavee.
