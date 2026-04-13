import { z } from "zod";
import { type ToolRegistrar, errorResult, jsonResult } from "./context.js";

export const registerConsoleTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "send_command",
    "Fire-and-forget: send a console command to a running server (e.g. `say hello`, `stop`, " +
      "`op alice`). The server must be in the running state — sending to a stopped server returns " +
      "502. Prefer `run_command` when you care about the command's output, since it atomically " +
      "captures the response and avoids racing with unrelated console noise.",
    {
      server_id: z.string().describe("Server identifier"),
      command: z.string().min(1).describe("The command to inject into the server console"),
    },
    async ({ server_id, command }) => {
      try {
        await ctx.client.sendCommand(server_id, command);
        const sentAtMs = Date.now();
        return jsonResult({
          ok: true,
          server_id,
          command,
          sent_at: new Date(sentAtMs).toISOString(),
          sent_at_ms: sentAtMs,
        });
      } catch (err) {
        const e = err as { status?: number; message?: string };
        if (e.status === 502) {
          return errorResult(
            `Cannot send command: server ${server_id} is not running (502 from panel).`,
          );
        }
        throw err;
      }
    },
  );

  server.tool(
    "run_command",
    "Send a command AND capture its console output atomically. This is the right tool when you " +
      "want to see the response to a command — it registers a listener before sending, waits up to " +
      "`wait_ms` for new lines, and returns only lines produced during the capture window. On " +
      "chatty servers this is dramatically more reliable than `send_command` + `tail_console`, " +
      "because you are not fighting unrelated chat/tick noise. Provide an `expect` regex to " +
      "short-circuit as soon as the expected response appears (e.g. `players online` after " +
      "`list`). Note: not every command produces output — some plugin commands emit nothing or " +
      "only log server-side. In that case you'll get an empty `lines` array after `wait_ms`.",
    {
      server_id: z.string().describe("Server identifier"),
      command: z.string().min(1).describe("The command to inject into the server console"),
      wait_ms: z
        .number()
        .int()
        .min(0)
        .max(30_000)
        .optional()
        .describe(
          "How long to collect output after the command is accepted by the panel. Default 1500.",
        ),
      expect: z
        .string()
        .optional()
        .describe(
          "JavaScript regex; as soon as a captured line matches, return early with matched=true. " +
            "Use to cut round-trip time and to disambiguate when the response is noisy.",
        ),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60_000)
        .optional()
        .describe(
          "Max time to wait for the underlying WS to be ready on first call. Default 10000ms.",
        ),
    },
    async ({ server_id, command, wait_ms, expect, ready_timeout_ms }) => {
      try {
        const result = await ctx.consoleHub.runCommand(server_id, command, {
          waitMs: wait_ms,
          expect,
          readyTimeoutMs: ready_timeout_ms,
        });
        return jsonResult(result);
      } catch (err) {
        const e = err as { status?: number; message?: string };
        if (e.status === 502) {
          return errorResult(
            `Cannot send command: server ${server_id} is not running (502 from panel).`,
          );
        }
        throw err;
      }
    },
  );

  server.tool(
    "tail_console",
    "Read recent console output from a server's rolling buffer. " +
      "On first call for a given server the MCP opens a persistent websocket to Wings and starts buffering; " +
      "subsequent calls return instantly from the buffer. The session is closed automatically after " +
      "~10 minutes of inactivity (configurable). Use `watch_server` to pin and prevent reaping. " +
      "Returns the lines plus the current server state. Use `match` to regex-filter noisy " +
      "consoles down to only the lines you care about.",
    {
      server_id: z.string().describe("Server identifier"),
      limit: z
        .number()
        .int()
        .min(1)
        .max(10_000)
        .optional()
        .describe("Max lines to return (most recent). Default: 100."),
      since_ms: z
        .number()
        .int()
        .optional()
        .describe(
          "Only return lines newer than this epoch-ms timestamp. Useful to poll incrementally.",
        ),
      match: z
        .string()
        .optional()
        .describe(
          "JavaScript regex; only lines matching are returned. Applied after limit/since. " +
            "Great for cutting chat/tick noise (e.g. `^\\[.*\\]: (Server|ERROR|WARN)`).",
        ),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60000)
        .optional()
        .describe("Max time to wait for the WS to authenticate on first call. Default 10000ms."),
    },
    async ({ server_id, limit, since_ms, match, ready_timeout_ms }) => {
      try {
        const result = await ctx.consoleHub.tail(server_id, {
          limit: limit ?? 100,
          sinceMs: since_ms,
          match,
          readyTimeoutMs: ready_timeout_ms,
        });
        return jsonResult(result);
      } catch (err) {
        return errorResult((err as Error).message);
      }
    },
  );

  server.tool(
    "wait_console",
    "Block until new console output arrives, or until `wait_ms` elapses, or until an `expect` " +
      "regex matches — whichever comes first. This is the counterpart to `run_command` for cases " +
      "where you are NOT sending a command but waiting for something to happen: " +
      "e.g. after `power_action(restart)`, wait for `Done \\(` to confirm the server booted; " +
      "or watch for a crash/exception to appear. Returns all lines received during the wait plus " +
      "(if `since_ms` is provided) any already-buffered lines with `ts >= since_ms`. If `expect` " +
      "matches a historical line in that window, the call returns instantly without waiting. " +
      "Prefer this over polling `tail_console` in a loop — it is cheaper and race-free.",
    {
      server_id: z.string().describe("Server identifier"),
      wait_ms: z
        .number()
        .int()
        .min(0)
        .max(120_000)
        .optional()
        .describe(
          "How long to wait for new output. Default 5000. Bump for slow events (server reboots, " +
            "large world loads). Max 120000 (2 minutes) — for longer waits, poll in a loop.",
        ),
      expect: z
        .string()
        .optional()
        .describe(
          "JavaScript regex; as soon as a captured (or historical) line matches, return early " +
            "with matched=true. Typical examples: `Done \\(\\d` (server boot complete), " +
            "`\\[Server thread/ERROR\\]` (any error), `joined the game` (player connect).",
        ),
      since_ms: z
        .number()
        .int()
        .optional()
        .describe(
          "Epoch-ms; buffered lines with ts >= since_ms are included in the result and scanned " +
            "for the `expect` regex before waiting. Use the `sent_at_ms` / `sentAtMs` field " +
            "returned by the preceding action (e.g. send_command, run_command) to bridge the gap.",
        ),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60_000)
        .optional()
        .describe("Max time to wait for the WS to be ready on first call. Default 10000ms."),
    },
    async ({ server_id, wait_ms, expect, since_ms, ready_timeout_ms }) => {
      try {
        const result = await ctx.consoleHub.waitForOutput(server_id, {
          waitMs: wait_ms,
          expect,
          sinceMs: since_ms,
          readyTimeoutMs: ready_timeout_ms,
        });
        return jsonResult(result);
      } catch (err) {
        return errorResult((err as Error).message);
      }
    },
  );

  server.tool(
    "watch_server",
    "Pin a server's console session so it is never reaped by the idle TTL. " +
      "Use this when you want to continuously monitor a server (e.g. across multiple Claude turns). " +
      "Call `unwatch_server` to release the pin and let normal TTL behavior resume.",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const result = ctx.consoleHub.watch(server_id);
      return jsonResult(result);
    },
  );

  server.tool(
    "unwatch_server",
    "Remove the pin set by `watch_server`. The session itself is not closed " +
      "immediately — it will be reaped after the normal idle TTL elapses if no further access.",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const result = ctx.consoleHub.unwatch(server_id);
      return jsonResult(result);
    },
  );

  server.tool(
    "list_console_sessions",
    "List all console sessions currently held by the MCP server: which servers are watched, " +
      "their current state, buffered line count, last access time, and pin status. " +
      "Useful for introspection and debugging.",
    {},
    async () => {
      const sessions = ctx.consoleHub.list();
      return jsonResult({ sessions });
    },
  );
};
