import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerBackupTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_backups",
    "List backups with uuid, name, size, status, and lock state. Paginated.",
    {
      server_id: z.string().describe("Server identifier"),
      page: z.number().int().min(1).optional(),
      per_page: z.number().int().min(1).max(50).optional(),
    },
    async ({ server_id, page, per_page }) => {
      const data = await ctx.client.listBackups(server_id, { page, perPage: per_page });
      return jsonResult(data);
    },
  );

  server.tool(
    "get_backup",
    "Get details of a single backup by its uuid.",
    {
      server_id: z.string().describe("Server identifier"),
      backup_uuid: z.string().describe("Backup UUID"),
    },
    async ({ server_id, backup_uuid }) => {
      const data = await ctx.client.getBackup(server_id, backup_uuid);
      return jsonResult(data);
    },
  );

  server.tool(
    "create_backup",
    "Create a backup (async). Poll get_backup for completion.",
    {
      server_id: z.string().describe("Server identifier"),
      name: z.string().max(191).optional().describe("Optional human-readable name"),
      ignored: z
        .string()
        .optional()
        .describe(
          "Newline-separated list of glob patterns to exclude from the backup (matches .pteroignore syntax)",
        ),
      is_locked: z
        .boolean()
        .optional()
        .describe("If true, prevent deletion until explicitly unlocked"),
    },
    async ({ server_id, name, ignored, is_locked }) => {
      const data = await ctx.client.createBackup(server_id, {
        name,
        ignored,
        isLocked: is_locked,
      });
      return jsonResult(data);
    },
  );

  server.tool(
    "delete_backup",
    "Delete a backup. Fails if the backup is locked — call `toggle_backup_lock` first.",
    {
      server_id: z.string().describe("Server identifier"),
      backup_uuid: z.string().describe("Backup UUID"),
    },
    async ({ server_id, backup_uuid }) => {
      await ctx.client.deleteBackup(server_id, backup_uuid);
      return jsonResult({ ok: true, server_id, backup_uuid, deleted: true });
    },
  );

  server.tool(
    "toggle_backup_lock",
    "Toggle the lock state of a backup. Locked backups cannot be deleted.",
    {
      server_id: z.string().describe("Server identifier"),
      backup_uuid: z.string().describe("Backup UUID"),
    },
    async ({ server_id, backup_uuid }) => {
      const data = await ctx.client.toggleBackupLock(server_id, backup_uuid);
      return jsonResult(data);
    },
  );

  server.tool(
    "restore_backup",
    "Restore backup over server files. DESTRUCTIVE if truncate=true (wipes first).",
    {
      server_id: z.string().describe("Server identifier"),
      backup_uuid: z.string().describe("Backup UUID"),
      truncate: z
        .boolean()
        .optional()
        .describe("Wipe existing server files before restoring. Default: false."),
    },
    async ({ server_id, backup_uuid, truncate }) => {
      await ctx.client.restoreBackup(server_id, backup_uuid, truncate ?? false);
      return jsonResult({
        ok: true,
        server_id,
        backup_uuid,
        truncate: truncate ?? false,
        started_at: new Date().toISOString(),
      });
    },
  );

  server.tool(
    "get_backup_download_url",
    "Get a signed, time-limited download URL for a backup.",
    {
      server_id: z.string().describe("Server identifier"),
      backup_uuid: z.string().describe("Backup UUID"),
    },
    async ({ server_id, backup_uuid }) => {
      const data = await ctx.client.getBackupDownloadUrl(server_id, backup_uuid);
      return jsonResult(data);
    },
  );
};
