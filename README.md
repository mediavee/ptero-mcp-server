# ptero-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants operate game servers managed by a [Pterodactyl](https://pterodactyl.io) panel. Power actions, live console, commands, backups, schedules, activity log — with a persistent rolling buffer so "what just happened on the server" is always one tool call away.

---

## Overview

Pterodactyl's REST API is enough to start/stop containers and manage resources, but it does **not** give you a natural way to read live console output, send a command and see its reply, or react to events as they happen. This server fills that gap:

- It keeps a **persistent WebSocket** open to the Wings daemon for every server you touch, with automatic reconnect and JWT refresh.
- Each session has a **rolling line buffer** (default 5000 lines), so `tail_console` returns the recent past instantly — no polling, no missed output.
- A dedicated `run_command` tool **atomically sends a command and captures its reply**, with an optional regex short-circuit. No race on chatty consoles.
- A `wait_console` tool blocks the current tool call until a matching line arrives — ideal for "restart and tell me when it's back".
- An **HTTP Server-Sent Events endpoint** (`/streams/:serverId`) streams new lines to arbitrary clients, designed for Claude Code's `Monitor` tool to enable long-running async watchdogs.

The full Pterodactyl client API surface — power, backups, databases, schedules, activity log, resources — is also exposed as typed MCP tools.

## Features

- **Live console** with persistent WebSocket buffering and atomic command/reply semantics
- **Power management** (start/stop/restart/kill) returning timestamps for precise event correlation
- **Backups**: list, create, delete, lock, restore, download
- **Databases**: list, create, rotate password, delete
- **Schedules + tasks**: full CRUD plus `execute_schedule` for manual triggers
- **Activity log** with actor relationships included (answer "who restarted X at 14:32?")
- **SSE streaming** for push-style async monitoring via `curl -N` or Claude Code's `Monitor`
- **Single process, multi-server**: handles any number of Pterodactyl servers concurrently
- **Bearer-token authenticated** HTTP transport, suitable for private-network or VPN-fronted deployments

## Quick start

### 1. Prerequisites

- A Pterodactyl panel with a Client API key (`Account → API Credentials`)
- Node 20+ **or** Docker

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in the three required values:

```env
PTERODACTYL_URL=https://panel.example.com
PTERODACTYL_API_KEY=ptlc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
MCP_AUTH_TOKEN=$(openssl rand -hex 32)
```

### 3. Run with Docker Compose

```bash
docker compose up -d --build
```

The server listens on `http://0.0.0.0:3000/mcp` by default. A `/healthz` endpoint is exposed for container health checks.

### 4. Connect Claude Code

```bash
claude mcp add ptero --transport http http://localhost:3000/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN"
```

For Claude Desktop or other clients that use JSON config:

```json
{
  "mcpServers": {
    "ptero": {
      "type": "http",
      "url": "http://localhost:3000/mcp",
      "headers": {
        "Authorization": "Bearer <your token>"
      }
    }
  }
}
```

Once connected, the skill file `SKILL.md` bundled at the repo root is picked up automatically by clients that support it, giving the assistant concrete methodology for common flows (restart, diagnose, send command, etc.).

## Tools

Thirty MCP tools grouped into seven categories. See [`SKILL.md`](./SKILL.md) for composition patterns and methodology.

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

## Console architecture

The console subsystem is what makes this server worth its own repo.

### The problem

Pterodactyl's `POST /servers/:id/command` is fire-and-forget: it does not return the command's output. The only way to observe output is to connect to Wings over a WebSocket and read the stream. On a busy server (populated survival, verbose plugins), hundreds of unrelated lines can arrive per second, making it nearly impossible to correlate "this line is the reply to the command I just sent" without careful timing.

### How it works

`ConsoleHub` maintains one lazy-initialized WebSocket per server, with automatic reconnect, JWT refresh every 8 minutes, and a rolling ring buffer. On top of that, three distinct read patterns are exposed:

| Pattern | Tool | Mechanics |
|---|---|---|
| **Past** | `tail_console` | Returns lines already in the buffer, optionally filtered |
| **Future (sync)** | `wait_console` | Registers a transient listener, blocks up to `wait_ms`, short-circuits on `expect` regex. Optional `since_ms` also scans recently buffered lines — atomic, no race |
| **Command + reply** | `run_command` | Registers listener **before** sending the command, collects lines for a bounded window **after** the panel accepts it. `expect` regex short-circuits as soon as the reply appears |
| **Future (async)** | `/streams/:serverId` (SSE) | Streams matching lines to HTTP clients in real time; sessions with active subscribers are exempt from idle reaping |

The listener API is built on a single shared primitive (`captureWithListener`) so that adding new read patterns is cheap.

### Mental model

| Intent | Use |
|---|---|
| Read what happened recently | `tail_console` |
| Wait for one specific event in the current turn | `wait_console` |
| Send a command and capture its reply | `run_command` |
| Send a command and ignore the output | `send_command` |
| Watch passively over a long duration | `/streams/:serverId` + `Monitor` |

## SSE streaming endpoint

In addition to the MCP tools, the server exposes a raw Server-Sent Events endpoint for push-style consumption:

```
GET /streams/:serverId?match=<regex>&include_history_since=<epoch_ms>&ready_timeout_ms=<ms>
Authorization: Bearer <MCP_AUTH_TOKEN>
```

Events:

| Event | Meaning |
|---|---|
| `ready` | Subscription is live. Sent once. |
| `line` | A new (or historical, if `include_history_since` was set) matching console line. Payload: `{ ts, line }`. |
| `error` | Subscription failed to establish. Stream ends. |
| `:keep-alive` comment | Sent every 30 seconds to prevent proxies from closing the connection. |

Example — tail `abc12345` for any error or exception:

```bash
curl -N -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  "http://ptero-mcp:3000/streams/abc12345?match=ERROR%7CException%7Ccrash"
```

In Claude Code, plug that into the `Monitor` tool to get async interjections whenever a matching line appears — the conversation is not blocked while the watcher runs.

## Configuration

All configuration is via environment variables.

| Variable | Required | Default | Description |
|---|---|---|---|
| `PTERODACTYL_URL` | yes | — | Panel URL, no trailing slash |
| `PTERODACTYL_API_KEY` | yes | — | Client API key from `Account → API Credentials` |
| `MCP_AUTH_TOKEN` | yes | — | Bearer token required by every client. Generate with `openssl rand -hex 32` |
| `HTTP_HOST` | no | `0.0.0.0` | Bind host for the HTTP listener |
| `HTTP_PORT` | no | `3000` | Bind port |
| `CONSOLE_BUFFER_SIZE` | no | `5000` | Rolling buffer size per server (lines). Bump on chatty servers where 5000 lines covers only a few seconds |
| `CONSOLE_IDLE_TTL` | no | `600` | Seconds of inactivity before an unpinned, unwatched console session is closed |

The included `docker-compose.yml` also honors a `HTTP_BIND` variable for the *host-side* of the port mapping — set it to a Tailscale IP or `127.0.0.1` to avoid exposing the server on public interfaces.

## Development

```bash
npm install
npm run build      # tsc — one-shot build
npm run dev        # tsc --watch — rebuild on change
npm start          # node dist/index.js (after build)
```

The codebase is small and flat:

```
src/
├── index.ts           # Express + MCP HTTP transport + SSE endpoint + shutdown
├── auth.ts            # Bearer token middleware
├── config.ts          # Env var loader
├── ptero/
│   ├── client.ts      # Thin Pterodactyl Client API wrapper
│   └── console-hub.ts # Persistent WS hub, ring buffer, listener plumbing
└── tools/
    ├── register.ts    # Tool registration entry point
    ├── context.ts     # Shared helpers (jsonResult, errorResult, ToolContext)
    ├── servers.ts     # Discovery & state
    ├── power.ts       # power_action
    ├── console.ts     # tail / wait / run / send / watch / list sessions
    ├── activity.ts    # get_activity_log
    ├── backups.ts
    ├── databases.ts
    └── schedules.ts
```

## Deployment notes

- **Security.** `MCP_AUTH_TOKEN` is the only thing between the network and your Pterodactyl panel. Bind to a private interface (Tailscale, VPN, or loopback behind a trusted reverse proxy) in any real deployment. `.env` is in `.gitignore` — keep it that way.
- **Reverse proxies.** If you front the server with nginx / Caddy / Traefik, disable response buffering on `/streams/*` so SSE events are flushed immediately. The server already sets `X-Accel-Buffering: no`.
- **Multi-server.** A single process handles any number of Pterodactyl servers concurrently. Sessions are created lazily on first reference and reaped after `CONSOLE_IDLE_TTL` seconds of inactivity unless pinned (`watch_server`) or holding active SSE subscribers.
- **Graceful shutdown.** `SIGTERM` / `SIGINT` closes all sessions, transports, and SSE connections, then exits. A 10-second timeout forces exit if anything hangs.

## License

Private. © Mediavee.
