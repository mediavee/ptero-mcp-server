# ptero-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants operate game servers managed by a [Pterodactyl](https://pterodactyl.io) panel. Power actions, live console, commands, backups, schedules, activity log — with a persistent rolling buffer so "what just happened on the server" is always one tool call away.

Built on **[FastMCP 3.x](https://gofastmcp.com)** + Python 3.12 + asyncio. Single-tenant stdio transport — one process serves one panel + one operator key.

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
- **Single process, multi-server**: handles any number of Pterodactyl servers concurrently against one panel
- **Structured JSON-capable logging** on stderr, retry-aware HTTP client (3 attempts on 5xx with exponential backoff)

## Quick start

### 1. Prerequisites

- A Pterodactyl panel + a Client API key (`Account → API Credentials`)
- Python **3.12+** with [`uv`](https://github.com/astral-sh/uv) — recommended

### 2. Install

```bash
uv sync
```

Or, for transient use straight from the repo:

```bash
uv run --from . ptero-mcp
```

### 3. Connect Claude Code / Claude Desktop

Each MCP client entry spawns its own subprocess with the panel URL + key in its environment. Run **one entry per (panel, operator key)** pair you want to manage.

**Claude Code** (`claude mcp add`):

```bash
claude mcp add ptero-prod -- env \
  PTERODACTYL_URL=https://panel.prod.example.com \
  PTERODACTYL_KEY=$PTERO_PROD_KEY \
  ptero-mcp

claude mcp add ptero-staging -- env \
  PTERODACTYL_URL=https://panel.staging.example.com \
  PTERODACTYL_KEY=$PTERO_STAGING_KEY \
  ptero-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ptero-prod": {
      "command": "ptero-mcp",
      "env": {
        "PTERODACTYL_URL": "https://panel.prod.example.com",
        "PTERODACTYL_KEY": "<your prod client API key>"
      }
    }
  }
}
```

If `ptero-mcp` is not on the client's `PATH`, point at the absolute uv-managed binary or use `uvx --from <repo-path> ptero-mcp` as the `command`.

Once connected, the skill file [`SKILL.md`](./SKILL.md) at the repo root is picked up automatically by clients that support it, giving the assistant concrete methodology for common flows (restart, diagnose, send command, etc.).

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

| Variable                | Required | Default | Description                                                                       |
|-------------------------|----------|---------|-----------------------------------------------------------------------------------|
| `PTERODACTYL_URL`       | yes      | —       | Base URL of the Pterodactyl panel (e.g. `https://panel.example.com`)              |
| `PTERODACTYL_KEY`       | yes      | —       | Pterodactyl Client API key                                                        |
| `CONSOLE_BUFFER_SIZE`   | no       | `5000`  | Rolling buffer size per server. Bump on chatty servers where 5000 lines covers only a few seconds |
| `CONSOLE_IDLE_TTL`      | no       | `600`   | Seconds of inactivity before an unpinned console session is closed                |
| `LOG_LEVEL`             | no       | `INFO`  | Logger level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)                                |
| `LOG_JSON`              | no       | `false` | Emit logs as JSON (recommended for production aggregation)                        |

Logs go to **stderr**. stdout is reserved for the MCP JSON-RPC stream.

## Development

```bash
uv sync                          # create .venv + install deps
uv run ptero-mcp                 # run the server (reads .env)
uv run python -m ptero_mcp       # equivalent
uv run ruff check src            # lint
uv run ruff format src           # format
```

The codebase is small, async-first, and flat:

```
src/ptero_mcp/
├── __main__.py        # python -m ptero_mcp / console script entry
├── server.py          # FastMCP app + stdio transport + console hub lifecycle
├── config.py          # pydantic-settings: typed env loading
├── logging.py         # structlog config (stderr, text or JSON)
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

## Operational notes

- **One process per (panel, operator key) pair.** Each MCP client entry spawns its own `ptero-mcp` subprocess with its own creds in env. To switch panels or operators, switch entries.
- **Console sessions are reaped** after `CONSOLE_IDLE_TTL` seconds of inactivity unless pinned via `watch_server`.
- **Graceful shutdown.** `SIGTERM` / `SIGINT` closes all WebSocket sessions and the httpx client, then exits.

## License

Private. © Mediavee.
