import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

const cronField = z
  .string()
  .describe("Cron field value, e.g. '*', '*/5', '0', '1,15'");

export const registerScheduleTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_schedules",
    "List all schedules defined on a server, with their tasks. " +
      "Each schedule has a cron expression and a list of sequential tasks (command/power/backup).",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const data = await ctx.client.listSchedules(server_id);
      return jsonResult(data);
    },
  );

  server.tool(
    "get_schedule",
    "Get details of a single schedule by id, including its tasks.",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
    },
    async ({ server_id, schedule_id }) => {
      const data = await ctx.client.getSchedule(server_id, schedule_id);
      return jsonResult(data);
    },
  );

  server.tool(
    "create_schedule",
    "Create a new schedule for a server. The cron fields use Pterodactyl's syntax " +
      "(standard 5-field cron: minute, hour, day_of_month, month, day_of_week). " +
      "After creation, add tasks via `create_schedule_task`.",
    {
      server_id: z.string().describe("Server identifier"),
      name: z.string().min(1).max(191),
      is_active: z.boolean().optional().describe("Default true"),
      only_when_online: z
        .boolean()
        .optional()
        .describe("Skip execution when server is not running. Default false."),
      minute: cronField,
      hour: cronField,
      day_of_month: cronField,
      month: cronField,
      day_of_week: cronField,
    },
    async ({
      server_id,
      name,
      is_active,
      only_when_online,
      minute,
      hour,
      day_of_month,
      month,
      day_of_week,
    }) => {
      const data = await ctx.client.createSchedule(server_id, {
        name,
        isActive: is_active,
        onlyWhenOnline: only_when_online,
        minute,
        hour,
        dayOfMonth: day_of_month,
        month,
        dayOfWeek: day_of_week,
      });
      return jsonResult(data);
    },
  );

  server.tool(
    "update_schedule",
    "Update an existing schedule. All fields are required (panel uses POST as full replace).",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
      name: z.string().min(1).max(191),
      is_active: z.boolean(),
      only_when_online: z.boolean().optional(),
      minute: cronField,
      hour: cronField,
      day_of_month: cronField,
      month: cronField,
      day_of_week: cronField,
    },
    async ({
      server_id,
      schedule_id,
      name,
      is_active,
      only_when_online,
      minute,
      hour,
      day_of_month,
      month,
      day_of_week,
    }) => {
      const data = await ctx.client.updateSchedule(server_id, schedule_id, {
        name,
        isActive: is_active,
        onlyWhenOnline: only_when_online,
        minute,
        hour,
        dayOfMonth: day_of_month,
        month,
        dayOfWeek: day_of_week,
      });
      return jsonResult(data);
    },
  );

  server.tool(
    "delete_schedule",
    "Delete a schedule and all its tasks.",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
    },
    async ({ server_id, schedule_id }) => {
      await ctx.client.deleteSchedule(server_id, schedule_id);
      return jsonResult({ ok: true, server_id, schedule_id, deleted: true });
    },
  );

  server.tool(
    "execute_schedule",
    "Execute a schedule immediately, ignoring its cron expression and active flag. " +
      "Useful for testing or triggering an ad-hoc backup/restart.",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
    },
    async ({ server_id, schedule_id }) => {
      await ctx.client.executeSchedule(server_id, schedule_id);
      return jsonResult({
        ok: true,
        server_id,
        schedule_id,
        triggered_at: new Date().toISOString(),
      });
    },
  );

  server.tool(
    "create_schedule_task",
    "Add a task to a schedule. Tasks run sequentially in `sequence_id` order, with `time_offset` " +
      "seconds delay before the task fires (relative to schedule trigger or previous task). " +
      "Action types: `command` (sends `payload` to console), `power` (`payload` = start/stop/restart/kill), " +
      "`backup` (`payload` = optional ignored files).",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
      action: z.enum(["command", "power", "backup"]),
      payload: z
        .string()
        .optional()
        .describe("Required for command/power; optional for backup (ignored files list)"),
      time_offset: z
        .number()
        .int()
        .min(0)
        .max(900)
        .describe("Seconds to wait before firing (0-900)"),
      sequence_id: z
        .number()
        .int()
        .min(1)
        .optional()
        .describe("Position in the task sequence (1-based). Defaults to last."),
      continue_on_failure: z.boolean().optional().describe("Default false"),
    },
    async ({
      server_id,
      schedule_id,
      action,
      payload,
      time_offset,
      sequence_id,
      continue_on_failure,
    }) => {
      const data = await ctx.client.createScheduleTask(server_id, schedule_id, {
        action,
        payload,
        timeOffset: time_offset,
        sequenceId: sequence_id,
        continueOnFailure: continue_on_failure,
      });
      return jsonResult(data);
    },
  );

  server.tool(
    "update_schedule_task",
    "Update an existing task on a schedule. All fields are required.",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
      task_id: z.number().int().positive(),
      action: z.enum(["command", "power", "backup"]),
      payload: z.string().optional(),
      time_offset: z.number().int().min(0).max(900),
      sequence_id: z.number().int().min(1).optional(),
      continue_on_failure: z.boolean().optional(),
    },
    async ({
      server_id,
      schedule_id,
      task_id,
      action,
      payload,
      time_offset,
      sequence_id,
      continue_on_failure,
    }) => {
      const data = await ctx.client.updateScheduleTask(server_id, schedule_id, task_id, {
        action,
        payload,
        timeOffset: time_offset,
        sequenceId: sequence_id,
        continueOnFailure: continue_on_failure,
      });
      return jsonResult(data);
    },
  );

  server.tool(
    "delete_schedule_task",
    "Delete a task from a schedule. Subsequent tasks have their sequence_id decremented automatically.",
    {
      server_id: z.string().describe("Server identifier"),
      schedule_id: z.number().int().positive(),
      task_id: z.number().int().positive(),
    },
    async ({ server_id, schedule_id, task_id }) => {
      await ctx.client.deleteScheduleTask(server_id, schedule_id, task_id);
      return jsonResult({
        ok: true,
        server_id,
        schedule_id,
        task_id,
        deleted: true,
      });
    },
  );
};
