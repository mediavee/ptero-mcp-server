import { randomUUID } from "node:crypto";
import express, { type Request, type Response } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import { loadConfig } from "./config.js";
import { PterodactylClient } from "./ptero/client.js";
import { ConsoleHub, type ConsoleLine } from "./ptero/console-hub.js";
import { bearerAuth } from "./auth.js";
import { registerAllTools } from "./tools/register.js";
import type { ToolContext } from "./tools/context.js";

const SERVER_INFO = {
  name: "ptero-mcp",
  version: "0.1.0",
};

async function main(): Promise<void> {
  const config = loadConfig();
  const client = new PterodactylClient(config);
  const consoleHub = new ConsoleHub(config, client);

  const ctx: ToolContext = { config, client, consoleHub };

  // ─────────────────────────── HTTP setup ───────────────────────────

  const app = express();
  app.use(express.json({ limit: "4mb" }));

  // Unauthenticated health check for Docker / monitoring.
  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok", uptime: process.uptime() });
  });

  // All MCP + streaming endpoints require auth.
  app.use("/mcp", bearerAuth(config.authToken));
  app.use("/streams", bearerAuth(config.authToken));

  // Active transports keyed by MCP session id. Each Claude client gets its own
  // McpServer instance + transport, but they all share the same ToolContext
  // (and therefore the same console hub).
  const transports = new Map<string, StreamableHTTPServerTransport>();

  // Client → server requests (initialize, tool calls, etc.)
  app.post("/mcp", async (req: Request, res: Response) => {
    try {
      const sessionId = req.header("mcp-session-id");
      let transport: StreamableHTTPServerTransport;

      if (sessionId && transports.has(sessionId)) {
        transport = transports.get(sessionId)!;
      } else if (!sessionId && isInitializeRequest(req.body)) {
        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (id: string) => {
            transports.set(id, transport);
            console.log(`[mcp] session initialized: ${id}`);
          },
        });
        transport.onclose = () => {
          if (transport.sessionId) {
            transports.delete(transport.sessionId);
            console.log(`[mcp] session closed: ${transport.sessionId}`);
          }
        };

        const mcp = new McpServer(SERVER_INFO);
        registerAllTools(mcp, ctx);
        await mcp.connect(transport);
      } else {
        res.status(400).json({
          jsonrpc: "2.0",
          error: {
            code: -32000,
            message: "Bad Request: missing or invalid session id",
          },
          id: null,
        });
        return;
      }

      await transport.handleRequest(req, res, req.body);
    } catch (err) {
      console.error("[mcp] POST /mcp failed:", err);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal error" },
          id: null,
        });
      }
    }
  });

  // Server → client SSE stream (used by the SDK for notifications and
  // long-running responses).
  const handleSessionRequest = async (req: Request, res: Response) => {
    const sessionId = req.header("mcp-session-id");
    if (!sessionId || !transports.has(sessionId)) {
      res.status(400).send("Invalid or missing session id");
      return;
    }
    try {
      await transports.get(sessionId)!.handleRequest(req, res);
    } catch (err) {
      console.error("[mcp] session request failed:", err);
      if (!res.headersSent) res.status(500).end();
    }
  };

  app.get("/mcp", handleSessionRequest);
  app.delete("/mcp", handleSessionRequest);

  // ─────────────────────────── SSE streaming ───────────────────────────
  //
  // Server-Sent Events endpoint for push-style consumption of a server's
  // console output. Designed for Claude Code's `Monitor` tool and other
  // clients that want async notifications instead of explicit polling:
  //
  //   curl -N -H "Authorization: Bearer $TOKEN" \
  //     "http://host:3000/streams/abc12345?match=ERROR|crash"
  //
  // Query params:
  //   - match                 (optional) JS regex; only matching lines are emitted
  //   - include_history_since (optional) epoch-ms; replay buffered lines with ts >= this
  //   - ready_timeout_ms      (optional) max time to wait for the WS to be ready; default 10000
  //
  // Event types:
  //   - ready     Subscription established. Sent once.
  //   - line      A new matching console line. Payload: { ts, line }.
  //   - error     Something went wrong. Payload: { message }. Stream ends after.
  //   - :keep-alive comment every 30s to keep proxies from closing the connection.
  app.get("/streams/:serverId", async (req: Request, res: Response) => {
    const serverIdRaw = req.params.serverId;
    const serverId = Array.isArray(serverIdRaw) ? serverIdRaw[0] : serverIdRaw;
    if (typeof serverId !== "string" || serverId.length === 0) {
      res.status(400).json({ error: "Missing serverId path parameter" });
      return;
    }
    const matchParam = typeof req.query.match === "string" ? req.query.match : undefined;
    const historySinceParam =
      typeof req.query.include_history_since === "string"
        ? Number.parseInt(req.query.include_history_since, 10)
        : undefined;
    const readyTimeoutParam =
      typeof req.query.ready_timeout_ms === "string"
        ? Number.parseInt(req.query.ready_timeout_ms, 10)
        : undefined;

    let matchRegex: RegExp | null = null;
    if (matchParam) {
      try {
        matchRegex = new RegExp(matchParam);
      } catch (err) {
        res
          .status(400)
          .json({ error: `Invalid match regex: ${(err as Error).message}` });
        return;
      }
    }

    if (historySinceParam !== undefined && Number.isNaN(historySinceParam)) {
      res.status(400).json({ error: "include_history_since must be an integer (epoch ms)" });
      return;
    }
    if (readyTimeoutParam !== undefined && Number.isNaN(readyTimeoutParam)) {
      res.status(400).json({ error: "ready_timeout_ms must be an integer" });
      return;
    }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    // Prevent nginx (and other reverse proxies) from buffering the stream.
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders?.();

    const sendEvent = (event: string, data: unknown): void => {
      if (res.writableEnded) return;
      res.write(`event: ${event}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const emitLine = (line: ConsoleLine): void => {
      if (!matchRegex || matchRegex.test(line.line)) {
        sendEvent("line", line);
      }
    };

    sendEvent("ready", { serverId, match: matchParam ?? null });

    let unsubscribe: () => void = () => {};
    try {
      const sub = await consoleHub.subscribe(serverId, emitLine, {
        historySinceMs: historySinceParam,
        readyTimeoutMs: readyTimeoutParam,
      });
      unsubscribe = sub.unsubscribe;
      // Replay historical lines through the same match filter. Ordering is
      // preserved because subscribe() captured the snapshot atomically with
      // the listener registration.
      for (const line of sub.historicalLines) {
        emitLine(line);
      }
    } catch (err) {
      sendEvent("error", { message: (err as Error).message });
      res.end();
      return;
    }

    const keepAlive = setInterval(() => {
      if (res.writableEnded) return;
      res.write(": keep-alive\n\n");
    }, 30_000);
    keepAlive.unref?.();

    const cleanup = (): void => {
      clearInterval(keepAlive);
      unsubscribe();
      if (!res.writableEnded) res.end();
    };

    req.on("close", cleanup);
    req.on("error", cleanup);
  });

  const server = app.listen(config.httpPort, config.httpHost, () => {
    console.log(
      `[ptero-mcp] listening on http://${config.httpHost}:${config.httpPort}/mcp`,
    );
    console.log(
      `[ptero-mcp] sse streams: http://${config.httpHost}:${config.httpPort}/streams/:serverId`,
    );
    console.log(`[ptero-mcp] panel: ${config.pterodactylUrl}`);
    console.log(
      `[ptero-mcp] console buffer: ${config.consoleBufferSize} lines, idle TTL: ${config.consoleIdleTtlMs / 1000}s`,
    );
  });

  // ─────────────────────────── Shutdown ───────────────────────────

  const shutdown = (signal: string) => {
    console.log(`[ptero-mcp] received ${signal}, shutting down`);
    server.close(() => {
      consoleHub.shutdown();
      for (const transport of transports.values()) {
        try {
          transport.close();
        } catch {
          // ignore
        }
      }
      transports.clear();
      process.exit(0);
    });
    // SSE streams are long-lived connections; close them explicitly so the
    // listen socket can actually close. Safe no-op if nothing is connected.
    server.closeAllConnections?.();
    // Force exit after 10s if graceful shutdown hangs.
    setTimeout(() => {
      console.error("[ptero-mcp] forced exit after timeout");
      process.exit(1);
    }, 10_000).unref();
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((err) => {
  console.error("[ptero-mcp] fatal:", err);
  process.exit(1);
});
