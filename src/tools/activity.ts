import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerActivityTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "get_activity_log",
    "Read the audit/activity log for a server. " +
      "These are panel-level events: who started/stopped/restarted, who edited files, who created backups, schedule executions, etc. " +
      "This is NOT the console output of the server itself — for that, use `tail_console`. " +
      "Filter by event prefix (e.g. `server:power`, `server:console.command`, `server:backup`). " +
      "The acting user is included under `attributes.relationships.actor.attributes` " +
      "(username/email/uuid); if `actor` is null the event was triggered by the system " +
      "(schedule, automation) rather than a human.",
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
