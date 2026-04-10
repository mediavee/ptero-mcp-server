import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerServerTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_servers",
    "List all servers accessible to the configured Pterodactyl API key. " +
      "Returns paginated server descriptors with identifier, name, node, status, and resource limits. " +
      "Use this for discovery before calling any per-server tool.",
    {
      page: z.number().int().min(1).optional().describe("Page number (1-indexed)"),
      per_page: z
        .number()
        .int()
        .min(1)
        .max(100)
        .optional()
        .describe("Items per page (max 100)"),
    },
    async ({ page, per_page }) => {
      const data = await ctx.client.listServers({ page, perPage: per_page });
      return jsonResult(data);
    },
  );

  server.tool(
    "get_server",
    "Get full details of a single server: identifier, name, node, limits, " +
      "feature limits (databases/allocations/backups), startup command, docker image, " +
      "and current status (running/installing/suspended/etc).",
    {
      server_id: z
        .string()
        .describe("Server identifier (short UUID, visible in panel URLs and list_servers output)"),
    },
    async ({ server_id }) => {
      const data = await ctx.client.getServer(server_id);
      return jsonResult(data);
    },
  );

  server.tool(
    "get_resources",
    "Get current resource utilization for a server: state (running/offline/starting/stopping), " +
      "memory bytes, CPU absolute %, disk bytes, network rx/tx bytes, and uptime in ms. " +
      "This is a point-in-time snapshot — for historical/live data use tail_console which also includes stats.",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const data = await ctx.client.getResources(server_id);
      return jsonResult(data);
    },
  );
};
