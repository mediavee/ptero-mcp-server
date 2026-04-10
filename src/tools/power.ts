import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerPowerTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "power_action",
    "Send a power signal to a server. " +
      "`start` boots the container, `stop` requests a graceful shutdown, " +
      "`restart` is stop+start, `kill` SIGKILLs the process (data loss risk — only when stuck). " +
      "Action is asynchronous: the panel acknowledges immediately and the server transitions " +
      "in the background. Returns `sent_at_ms` so you can feed it directly to `wait_console` " +
      "as `since_ms` to block on the next state transition without missing any output.",
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
