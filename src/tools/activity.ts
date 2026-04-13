import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerActivityTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "get_activity_log",
    "Read panel audit log (power, commands, backups, file edits). Filter by event prefix. Includes acting user when available.",
    {
      server_id: z.string().describe("Server identifier"),
      page: z.number().int().min(1).optional(),
      per_page: z.number().int().min(1).max(100).optional(),
      event_filter: z
        .string()
        .optional()
        .describe(
          "Partial match on event name (e.g. 'power', 'backup', 'console.command'). Optional.",
        ),
      sort: z
        .enum(["timestamp", "-timestamp"])
        .optional()
        .describe("Sort by timestamp asc/desc. Default: panel default (descending)."),
    },
    async ({ server_id, page, per_page, event_filter, sort }) => {
      const data = await ctx.client.getActivityLog(server_id, {
        page,
        perPage: per_page,
        eventFilter: event_filter,
        sort,
      });
      return jsonResult(data);
    },
  );
};
