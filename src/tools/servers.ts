import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerServerTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_servers",
    "List all servers with identifier, name, node, status, and limits. Paginated.",
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
    "Get full server details: limits, feature limits, startup, docker image, status.",
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
    "Get current resource utilization: state, memory, CPU, disk, network, uptime.",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const data = await ctx.client.getResources(server_id);
      return jsonResult(data);
    },
  );
};
