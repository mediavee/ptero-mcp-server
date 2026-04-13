import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerPowerTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "power_action",
    "Send power signal (start/stop/restart/kill). Async — returns `sent_at_ms` for use with wait_console `since_ms`.",
    {
      server_id: z.string().describe("Server identifier"),
      signal: z
        .enum(["start", "stop", "restart", "kill"])
        .describe("Power signal to send"),
    },
    async ({ server_id, signal }) => {
      await ctx.client.sendPower(server_id, signal);
      const sentAtMs = Date.now();
      return jsonResult({
        ok: true,
        server_id,
        signal,
        sent_at: new Date(sentAtMs).toISOString(),
        sent_at_ms: sentAtMs,
      });
    },
  );
};
