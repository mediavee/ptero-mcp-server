import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerBackupTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_backups",
    "List backups for a server (paginated). Includes backup uuid, name, size, creation time, " +
      "lock status, and whether the backup completed successfully.",
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
    "Create a new backup for a server. Backup creation is asynchronous: this returns immediately " +
      "with the new backup descriptor (uuid, status). Poll `get_backup` to check completion.",
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
    "Restore a backup over the server's files. " +
      "DESTRUCTIVE: if `truncate=true`, all current files are deleted before restore. " +
      "Otherwise the archive is unpacked over existing files (still overwrites collisions). " +
      "The server must be in a stable state (not currently restoring or installing).",
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
    "Get a signed, time-limited download URL for a backup. " +
      "Useful for off-panel inspection or transferring a backup elsewhere.",
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
