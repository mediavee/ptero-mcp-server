function required(name: string): string {
  const value = process.env[name];
  if (!value || value.trim() === "") {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value.trim();
}

function optional(name: string, fallback: string): string {
  const value = process.env[name];
  return value && value.trim() !== "" ? value.trim() : fallback;
}

function intEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (Number.isNaN(parsed)) {
    throw new Error(`Invalid integer for env var ${name}: ${raw}`);
  }
  return parsed;
}

export interface Config {
  pterodactylUrl: string;
  pterodactylApiKey: string;
  authToken: string;
  httpHost: string;
  httpPort: number;
  consoleBufferSize: number;
  consoleIdleTtlMs: number;
}

export function loadConfig(): Config {
  const url = required("PTERODACTYL_URL").replace(/\/+$/, "");
  return {
    pterodactylUrl: url,
    pterodactylApiKey: required("PTERODACTYL_API_KEY"),
    authToken: required("MCP_AUTH_TOKEN"),
    httpHost: optional("HTTP_HOST", "0.0.0.0"),
    httpPort: intEnv("HTTP_PORT", 3000),
    consoleBufferSize: intEnv("CONSOLE_BUFFER_SIZE", 5000),
    consoleIdleTtlMs: intEnv("CONSOLE_IDLE_TTL", 600) * 1000,
  };
}
