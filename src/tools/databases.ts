import { z } from "zod";
import { type ToolRegistrar, jsonResult } from "./context.js";

export const registerDatabaseTools: ToolRegistrar = (server, ctx) => {
  server.tool(
    "list_databases",
    "List databases with name, host, port, and password.",
    {
      server_id: z.string().describe("Server identifier"),
    },
    async ({ server_id }) => {
      const data = await ctx.client.listDatabases(server_id);
      return jsonResult(data);
    },
  );

  server.tool(
    "create_database",
    "Create a new database. Requires available slots (see get_server feature_limits).",
    {
      server_id: z.string().describe("Server identifier"),
      database: z
        .string()
        .min(1)
        .max(48)
        .describe("Database name (will be prefixed by the panel, e.g. s5_<name>)"),
      remote: z
        .string()
        .describe(
          "Allowed remote host pattern (e.g. '%' for any, '192.168.%' for a subnet, '127.0.0.1' for local-only)",
        ),
    },
    async ({ server_id, database, remote }) => {
      const data = await ctx.client.createDatabase(server_id, database, remote);
      return jsonResult(data);
    },
  );

  server.tool(
    "rotate_database_password",
    "Generate a new random password for a database. The new password is returned in the response.",
    {
      server_id: z.string().describe("Server identifier"),
      database_id: z.string().describe("Database identifier (hashid from list_databases)"),
    },
    async ({ server_id, database_id }) => {
      const data = await ctx.client.rotateDatabasePassword(server_id, database_id);
      return jsonResult(data);
    },
  );

  server.tool(
    "delete_database",
    "Delete a database. DESTRUCTIVE — the data is gone.",
    {
      server_id: z.string().describe("Server identifier"),
      database_id: z.string().describe("Database identifier (hashid)"),
    },
    async ({ server_id, database_id }) => {
      await ctx.client.deleteDatabase(server_id, database_id);
      return jsonResult({ ok: true, server_id, database_id, deleted: true });
    },
  );
};
