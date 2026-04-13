import { z } from "zod";
import { type ToolRegistrar, errorResult, jsonResult } from "./context.js";

export const registerConsoleTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "send_command",
    "Fire-and-forget console command. Server must be running. Prefer run_command when you need the output.",
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
    "Send command and capture output atomically. Use `expect` regex to short-circuit on match. Returns only lines produced during the capture window.",
    {
      server_id: z.string().describe("Server identifier"),
      command: z.string().min(1).describe("The command to inject into the server console"),
      wait_ms: z
        .number()
        .int()
        .min(0)
        .max(30_000)
        .optional()
        .describe("Output collection time in ms. Default 1500."),
      expect: z
        .string()
        .optional()
        .describe("Regex; return early when a line matches (matched=true)."),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60_000)
        .optional()
        .describe("Max WS ready wait in ms. Default 10000."),
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
    "Read recent lines from the console ring buffer. Use `match` for regex filtering. First call opens a persistent WS; idle sessions are reaped after ~10 min.",
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
        .describe("Only return lines newer than this epoch-ms."),
      match: z
        .string()
        .optional()
        .describe("Regex filter; only matching lines are returned."),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60000)
        .optional()
        .describe("Max WS ready wait in ms. Default 10000."),
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
    "Block until new output arrives, `wait_ms` elapses, or `expect` regex matches. Use `since_ms` to include buffered history. Prefer over polling tail_console.",
    {
      server_id: z.string().describe("Server identifier"),
      wait_ms: z
        .number()
        .int()
        .min(0)
        .max(120_000)
        .optional()
        .describe("Wait time in ms. Default 5000. Max 120000."),
      expect: z
        .string()
        .optional()
        .describe("Regex; return early when a line matches (matched=true)."),
      since_ms: z
        .number()
        .int()
        .optional()
        .describe("Include buffered lines with ts >= since_ms and scan them for expect."),
      ready_timeout_ms: z
        .number()
        .int()
        .min(100)
        .max(60_000)
        .optional()
        .describe("Max WS ready wait in ms. Default 10000."),
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
    "Pin a console session to prevent idle reaping. Call unwatch_server to release.",
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
    "Remove pin from watch_server. Session reaped after normal idle TTL.",
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
    "List active console sessions with state, buffer size, and pin status.",
    {},
    async () => {
      const sessions = ctx.consoleHub.list();
      return jsonResult({ sessions });
    },
  );
};
