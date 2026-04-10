---
name: pterodactyl-ops
description: Operate Minecraft / game servers managed by a Pterodactyl panel — power actions (start/stop/restart/kill), live console tailing, console commands, activity logs, resources, backups, databases, and schedules. Use when the user asks to check, restart, diagnose, send commands to, or manage any server hosted on the panel.
---

# Pterodactyl operations

You have access to the `ptero-mcp` MCP server, which talks to a Pterodactyl panel and to the Wings daemon's websocket. The server keeps **persistent console websockets** open per server with a rolling buffer — so `tail_console` returns the **recent past**, not just what arrives after the call.

## When to use this skill

Triggers (any language):
- "redémarre / restart / stop / start / kill the server"
- "le serveur lag / crash / ne répond plus / a un souci"
- "qu'est-ce qui se passe sur <server>"
- "envoie la commande X au serveur"
- "regarde les logs / la console de <server>"
- "fais un backup / restore le backup"
- "qui a fait quoi sur <server>"
- "list mes serveurs"

If the user is debugging Minecraft from a node-side perspective (filesystem, plugins, JVM dumps), prefer the local tools / `mc-assistant`. **This skill is for panel-level operations and live console.** They are complementary.

## Tool inventory

### Discovery & state
- `list_servers` — paginated list. Use first if you don't know the server identifier.
- `get_server` — full descriptor (limits, status, docker image, startup).
- `get_resources` — point-in-time CPU/RAM/disk/network/uptime + state.

### Power
- `power_action` — `start` | `stop` | `restart` | `kill`. Async; confirm via tail or resources.

### Live console
- `tail_console` — read the rolling buffer (past). Filterable by `limit`, `since_ms`, `match` regex.
- `wait_console` — block until new lines arrive; short-circuit on `expect` regex.
- `run_command` — atomically send a command AND capture its response window. **Preferred whenever you care about the reply.**
- `send_command` — fire-and-forget. Only when you don't care about output.
- `watch_server` / `unwatch_server` — pin/unpin a session against the idle TTL.
- `list_console_sessions` — introspection of currently held sessions.

**Quick mental model:**
- Past → `tail_console`
- Future (short, synchronous, in this turn) → `wait_console`
- Future (long, async, in the background, multi-event) → SSE stream + `Monitor` tool (see below)
- Command + its response → `run_command`
- Fire and forget → `send_command`

### Async watchdog (rare)

For long-running passive monitoring (> 2 min, multi-event, running in the background while you do other work), the MCP also exposes a raw SSE endpoint: `GET /streams/:serverId?match=<regex>` with `Authorization: Bearer <token>`. Events: `ready`, `line`, `error`. Consume it via Claude Code's `Monitor` tool with `curl -N`.

**This is a niche path.** Use it only when the user explicitly asks to "watch" or "monitor" something passively over a long duration, or wants to react to multiple events. For everything else (restart confirmation, single event, command response), prefer `wait_console` — it is synchronous, portable, and does not require the user to have pre-configured the MCP base URL and token as environment variables.

### Audit
- `get_activity_log` — paginated panel events (power, console.command, file, backup, schedule…). NOT console output.

### Backups
- `list_backups`, `get_backup`, `create_backup`, `delete_backup`, `toggle_backup_lock`, `restore_backup`, `get_backup_download_url`.

### Databases
- `list_databases`, `create_database`, `rotate_database_password`, `delete_database`.

### Schedules (+ tasks)
- `list_schedules`, `get_schedule`, `create_schedule`, `update_schedule`, `delete_schedule`, `execute_schedule`.
- `create_schedule_task`, `update_schedule_task`, `delete_schedule_task`.

## Methodology

### When asked "what's wrong with server X"

Don't just run one tool. Compose:

1. **`get_resources`** — current state, CPU, memory pressure. Tells you if it's running, OOMing, or pegged.
2. **`tail_console` with `limit: 200`** — last 200 lines in the buffer. Look for stack traces, exceptions, "stopping server", crash messages. On a very chatty server, narrow with `match: "ERROR|WARN|Exception|Caused by|stopping|crash"` to skip chat/tick noise.
3. **`get_activity_log` with `event_filter: "power"`** — who recently restarted/killed it? Was it a manual action or a crash?
4. Synthesize: state + last words + recent operator actions = a coherent picture.

If `tail_console` returns an empty buffer or warning ("timed out"), the server may be offline or never produced output since the buffer was created — try `power_action: start` if appropriate, then re-tail after a few seconds.

### When asked "restart server X"

1. (Optional) `tail_console` with a small `limit` to make sure it's not in the middle of something (active backup, players doing important stuff).
2. `power_action(server_id=X, signal="restart")` — returns `sent_at_ms`, keep it.
3. `wait_console(server_id=X, wait_ms=60000, expect="Done \\(\\d", since_ms=<sent_at_ms>)` — blocks until the server logs its boot-complete line or until 60s. Passing `since_ms` bridges the tiny gap so no output is missed.
4. Report state from the captured lines: `matched=true` → the server is back; `matched=false` with some lines → still booting, tell the user; empty `lines` → something is very wrong, check `get_resources` and `get_activity_log`.

### When asked "send /command to X"

**Default: `run_command`.** Atomic send + capture, no race on noisy consoles.

- `run_command(server_id=X, command="list", expect="players online", wait_ms=1500)`
- Pick an `expect` regex distinctive to the reply you want — it short-circuits as soon as it matches.
- Bump `wait_ms` for slow commands (`forceload add`, `worldborder set`, heavy plugin queries).
- `matched=true` means the regex hit and the wait was cut short. `matched=false` with some lines means the regex didn't hit but output arrived — read it anyway.
- **Empty `lines` is normal** for commands that log nothing (`say`, `tellraw`, many plugin commands). Not a bug.

**Fallback `send_command` + later `tail_console`** only when the response arrives over many seconds (e.g. `profile` dump). Use the returned `sent_at_ms` as `since_ms` to filter.

### When asked "kill server X"

`kill` is SIGKILL — **data loss risk** for any unsaved state (Minecraft world saves, DB writes). Always:
1. Warn the user that this is destructive.
2. Try `stop` first unless explicitly told otherwise or unless `tail_console` shows the server is genuinely frozen.
3. Only `kill` after confirming.

### When asked to restore a backup

`restore_backup` is **destructive** when `truncate=true`. Always:
1. Confirm the backup uuid via `get_backup` first (verify it's `is_successful: true` and `completed_at` is set).
2. Confirm with the user before calling, especially with `truncate`.
3. The server will enter the `restoring_backup` state and become temporarily unusable.

### When asked to create a schedule

Cron fields use 5-field syntax: `minute hour day_of_month month day_of_week`. Use `*` for "every", `*/N` for intervals, `0,30` for lists. Example: backup every 6 hours = `minute=0, hour=*/6, day_of_month=*, month=*, day_of_week=*`.

After `create_schedule`, immediately add tasks via `create_schedule_task` — a schedule with no tasks does nothing.

## Reading tool output

Pterodactyl responses follow the JSON:API "fractal" envelope:

```json
{
  "object": "list",
  "data": [
    { "object": "server", "attributes": { ... } },
    ...
  ],
  "meta": { "pagination": { ... } }
}
```

Look at `attributes` for the actual fields. Single-item responses use `{ "object": "...", "attributes": { ... } }`.

For `get_activity_log`, each entry embeds the acting user under `attributes.relationships.actor.attributes` (username, email, uuid, admin flag). If `relationships.actor` is `null`, the event was triggered by the system (a schedule firing, an automation, the Wings daemon) rather than a human. This is how you answer "who restarted the server at 14:32?".

`tail_console` is **not** wrapped — it returns a flat object directly:

```json
{
  "serverId": "abc12345",
  "state": "running",
  "bufferedLines": 487,
  "returnedLines": 100,
  "lines": [
    { "ts": 1738000000000, "line": "[12:34:56] [Server thread/INFO]: ..." }
  ]
}
```

`ts` is epoch ms — useful for incremental polling via `since_ms`.

## Common pitfalls

- **Don't confuse `get_activity_log` with `tail_console`.** Activity log = audit (who clicked what); tail_console = actual server stdout.
- **Server identifiers are short hashes** (e.g. `abc12345`), not the full UUID. `list_servers` returns the identifier in `attributes.identifier`.
- **First `tail_console` call has a startup latency** (~1-3s) while the WS authenticates and replays the server's history. Subsequent calls are instant.
- **Buffer size is bounded** (default 5000 lines). For very long backlogs, the oldest lines have been evicted — note this in your analysis if relevant. On very chatty servers, use `match` on `tail_console` to filter noise early.
- **`send_command` / `run_command` return a clean 502 error if the server isn't running.** Don't preflight with `get_resources` — just handle the error if it comes. Saves a round-trip.
- **Console buffer is per MCP-server instance.** If the MCP server restarted, all buffers are empty until new output arrives.
