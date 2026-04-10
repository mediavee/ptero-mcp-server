import type { Config } from "../config.js";

export class PterodactylError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = "PterodactylError";
  }
}

export type PowerSignal = "start" | "stop" | "restart" | "kill";

export interface PaginationParams {
  page?: number;
  perPage?: number;
}

export interface WebsocketCredentials {
  token: string;
  socket: string;
}

/**
 * Thin wrapper around the Pterodactyl Client API.
 *
 * Only methods used by the MCP tools are implemented. Responses are returned
 * as parsed JSON; the panel uses the JSON:API-ish "fractal" format with
 * `object`/`attributes` envelopes — callers are responsible for unwrapping.
 */
export class PterodactylClient {
  private readonly base: string;
  private readonly headers: Record<string, string>;

  constructor(config: Config) {
    this.base = `${config.pterodactylUrl}/api/client`;
    this.headers = {
      Accept: "application/json",
      Authorization: `Bearer ${config.pterodactylApiKey}`,
    };
  }

  private async request<T>(
    method: string,
    path: string,
    options: { query?: Record<string, string | number | boolean | undefined>; body?: unknown } = {},
  ): Promise<T> {
    const url = new URL(this.base + path);
    if (options.query) {
      for (const [k, v] of Object.entries(options.query)) {
        if (v !== undefined) url.searchParams.set(k, String(v));
      }
    }

    const init: RequestInit = {
      method,
      headers: { ...this.headers },
    };

    if (options.body !== undefined) {
      init.body = JSON.stringify(options.body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }

    const res = await fetch(url, init);

    if (res.status === 204) {
      return undefined as T;
    }

    const text = await res.text();
    let parsed: unknown = text;
    if (text.length > 0) {
      try {
        parsed = JSON.parse(text);
      } catch {
        // leave as raw text
      }
    }

    if (!res.ok) {
      throw new PterodactylError(
        `Pterodactyl API ${method} ${path} failed: ${res.status} ${res.statusText}`,
        res.status,
        parsed,
      );
    }

    return parsed as T;
  }

  // ───────────────────────────────────────── Servers ─────────────────────────────────────────

  listServers(params: PaginationParams = {}) {
    return this.request<unknown>("GET", "", {
      query: { page: params.page, per_page: params.perPage },
    });
  }

  getServer(serverId: string) {
    return this.request<unknown>("GET", `/servers/${serverId}`);
  }

  getResources(serverId: string) {
    return this.request<unknown>("GET", `/servers/${serverId}/resources`);
  }

  // ───────────────────────────────────────── Power ───────────────────────────────────────────

  sendPower(serverId: string, signal: PowerSignal) {
    return this.request<void>("POST", `/servers/${serverId}/power`, {
      body: { signal },
    });
  }

  // ───────────────────────────────────────── Console ─────────────────────────────────────────

  sendCommand(serverId: string, command: string) {
    return this.request<void>("POST", `/servers/${serverId}/command`, {
      body: { command },
    });
  }

  /**
   * Returns a one-time JWT and websocket URL for the Wings instance hosting
   * the server. The token expires after 10 minutes; refresh by calling this
   * again.
   */
  async getWebsocketCredentials(serverId: string): Promise<WebsocketCredentials> {
    const data = await this.request<{ data: WebsocketCredentials }>(
      "GET",
      `/servers/${serverId}/websocket`,
    );
    return data.data;
  }

  // ───────────────────────────────────────── Activity ────────────────────────────────────────

  getActivityLog(
    serverId: string,
    params: PaginationParams & { eventFilter?: string; sort?: "timestamp" | "-timestamp" } = {},
  ) {
    return this.request<unknown>("GET", `/servers/${serverId}/activity`, {
      query: {
        page: params.page,
        per_page: params.perPage,
        "filter[event]": params.eventFilter,
        sort: params.sort,
        // Without this, the panel omits the actor relationship and entries
        // only carry an anonymous event type — useless for "who did X".
        include: "actor",
      },
    });
  }

  // ───────────────────────────────────────── Backups ─────────────────────────────────────────

  listBackups(serverId: string, params: PaginationParams = {}) {
    return this.request<unknown>("GET", `/servers/${serverId}/backups`, {
      query: { page: params.page, per_page: params.perPage },
    });
  }

  getBackup(serverId: string, backupUuid: string) {
    return this.request<unknown>("GET", `/servers/${serverId}/backups/${backupUuid}`);
  }

  createBackup(
    serverId: string,
    options: { name?: string; ignored?: string; isLocked?: boolean } = {},
  ) {
    return this.request<unknown>("POST", `/servers/${serverId}/backups`, {
      body: {
        name: options.name,
        ignored: options.ignored,
        is_locked: options.isLocked,
      },
    });
  }

  deleteBackup(serverId: string, backupUuid: string) {
    return this.request<void>("DELETE", `/servers/${serverId}/backups/${backupUuid}`);
  }

  toggleBackupLock(serverId: string, backupUuid: string) {
    return this.request<unknown>(
      "POST",
      `/servers/${serverId}/backups/${backupUuid}/lock`,
    );
  }

  restoreBackup(serverId: string, backupUuid: string, truncate = false) {
    return this.request<void>(
      "POST",
      `/servers/${serverId}/backups/${backupUuid}/restore`,
      { body: { truncate } },
    );
  }

  getBackupDownloadUrl(serverId: string, backupUuid: string) {
    return this.request<{ object: string; attributes: { url: string } }>(
      "GET",
      `/servers/${serverId}/backups/${backupUuid}/download`,
    );
  }

  // ───────────────────────────────────────── Databases ───────────────────────────────────────

  listDatabases(serverId: string) {
    return this.request<unknown>("GET", `/servers/${serverId}/databases`, {
      query: { include: "password" },
    });
  }

  createDatabase(serverId: string, database: string, remote: string) {
    return this.request<unknown>("POST", `/servers/${serverId}/databases`, {
      body: { database, remote },
    });
  }

  rotateDatabasePassword(serverId: string, databaseId: string) {
    return this.request<unknown>(
      "POST",
      `/servers/${serverId}/databases/${databaseId}/rotate-password`,
    );
  }

  deleteDatabase(serverId: string, databaseId: string) {
    return this.request<void>(
      "DELETE",
      `/servers/${serverId}/databases/${databaseId}`,
    );
  }

  // ───────────────────────────────────────── Schedules ───────────────────────────────────────

  listSchedules(serverId: string) {
    return this.request<unknown>("GET", `/servers/${serverId}/schedules`);
  }

  getSchedule(serverId: string, scheduleId: number) {
    return this.request<unknown>("GET", `/servers/${serverId}/schedules/${scheduleId}`);
  }

  createSchedule(
    serverId: string,
    schedule: {
      name: string;
      isActive?: boolean;
      onlyWhenOnline?: boolean;
      minute: string;
      hour: string;
      dayOfMonth: string;
      month: string;
      dayOfWeek: string;
    },
  ) {
    return this.request<unknown>("POST", `/servers/${serverId}/schedules`, {
      body: {
        name: schedule.name,
        is_active: schedule.isActive ?? true,
        only_when_online: schedule.onlyWhenOnline ?? false,
        minute: schedule.minute,
        hour: schedule.hour,
        day_of_month: schedule.dayOfMonth,
        month: schedule.month,
        day_of_week: schedule.dayOfWeek,
      },
    });
  }

  updateSchedule(
    serverId: string,
    scheduleId: number,
    schedule: {
      name: string;
      isActive: boolean;
      onlyWhenOnline?: boolean;
      minute: string;
      hour: string;
      dayOfMonth: string;
      month: string;
      dayOfWeek: string;
    },
  ) {
    return this.request<unknown>("POST", `/servers/${serverId}/schedules/${scheduleId}`, {
      body: {
        name: schedule.name,
        is_active: schedule.isActive,
        only_when_online: schedule.onlyWhenOnline ?? false,
        minute: schedule.minute,
        hour: schedule.hour,
        day_of_month: schedule.dayOfMonth,
        month: schedule.month,
        day_of_week: schedule.dayOfWeek,
      },
    });
  }

  deleteSchedule(serverId: string, scheduleId: number) {
    return this.request<void>("DELETE", `/servers/${serverId}/schedules/${scheduleId}`);
  }

  executeSchedule(serverId: string, scheduleId: number) {
    return this.request<void>("POST", `/servers/${serverId}/schedules/${scheduleId}/execute`);
  }

  createScheduleTask(
    serverId: string,
    scheduleId: number,
    task: {
      action: "command" | "power" | "backup";
      payload?: string;
      timeOffset: number;
      sequenceId?: number;
      continueOnFailure?: boolean;
    },
  ) {
    return this.request<unknown>(
      "POST",
      `/servers/${serverId}/schedules/${scheduleId}/tasks`,
      {
        body: {
          action: task.action,
          payload: task.payload ?? "",
          time_offset: task.timeOffset,
          sequence_id: task.sequenceId,
          continue_on_failure: task.continueOnFailure ?? false,
        },
      },
    );
  }

  updateScheduleTask(
    serverId: string,
    scheduleId: number,
    taskId: number,
    task: {
      action: "command" | "power" | "backup";
      payload?: string;
      timeOffset: number;
      sequenceId?: number;
      continueOnFailure?: boolean;
    },
  ) {
    return this.request<unknown>(
      "POST",
      `/servers/${serverId}/schedules/${scheduleId}/tasks/${taskId}`,
      {
        body: {
          action: task.action,
          payload: task.payload ?? "",
          time_offset: task.timeOffset,
          sequence_id: task.sequenceId,
          continue_on_failure: task.continueOnFailure ?? false,
        },
      },
    );
  }

  deleteScheduleTask(serverId: string, scheduleId: number, taskId: number) {
    return this.request<void>(
      "DELETE",
      `/servers/${serverId}/schedules/${scheduleId}/tasks/${taskId}`,
    );
  }
}
