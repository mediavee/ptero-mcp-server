import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolContext } from "./context.js";
import { registerServerTools } from "./servers.js";
import { registerPowerTools } from "./power.js";
import { registerConsoleTools } from "./console.js";
import { registerActivityTools } from "./activity.js";
import { registerBackupTools } from "./backups.js";
import { registerDatabaseTools } from "./databases.js";
import { registerScheduleTools } from "./schedules.js";

export function registerAllTools(server: McpServer, ctx: ToolContext): void {
  registerServerTools(server, ctx);
  registerPowerTools(server, ctx);
  registerConsoleTools(server, ctx);
  registerActivityTools(server, ctx);
  registerBackupTools(server, ctx);
  registerDatabaseTools(server, ctx);
  registerScheduleTools(server, ctx);
}
