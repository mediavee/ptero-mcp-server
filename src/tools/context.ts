import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { Config } from "../config.js";
import type { PterodactylClient } from "../ptero/client.js";
import type { ConsoleHub } from "../ptero/console-hub.js";

export interface ToolContext {
  config: Config;
  client: PterodactylClient;
  consoleHub: ConsoleHub;
}

export type ToolRegistrar = (server: McpServer, ctx: ToolContext) => void;

/**
 * Standard JSON content payload for tool responses. Pterodactyl responses are
 * already structured (fractal envelope) so we forward them verbatim — the LLM
 * is perfectly capable of reading them.
 */
export function jsonResult(data: unknown) {
  return {
    content: [
      {
        type: "text" as const,
        text: typeof data === "string" ? data : JSON.stringify(data),
      },
    ],
  };
}

export function errorResult(message: string) {
  return {
    isError: true,
    content: [{ type: "text" as const, text: message }],
  };
}
