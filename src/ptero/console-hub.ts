import WebSocket from "ws";
import type { Config } from "../config.js";
import type { PterodactylClient, WebsocketCredentials } from "./client.js";

export interface ConsoleLine {
  /** Epoch milliseconds when the line was received by the hub. */
  ts: number;
  /** Raw line text as emitted by the server console (may contain ANSI codes). */
  line: string;
}

interface RingBuffer<T> {
  push(item: T): void;
  toArray(): T[];
  size(): number;
}

function createRingBuffer<T>(capacity: number): RingBuffer<T> {
  const buf: T[] = [];
  return {
    push(item) {
      buf.push(item);
      if (buf.length > capacity) buf.shift();
    },
    toArray() {
      return buf.slice();
    },
    size() {
      return buf.length;
    },
  };
}

function compileRegex(pattern: string | undefined, label: string): RegExp | null {
  if (!pattern) return null;
  try {
    return new RegExp(pattern);
  } catch (err) {
    throw new Error(`Invalid ${label} regex "${pattern}": ${(err as Error).message}`);
  }
}

type LineListener = (line: ConsoleLine) => void;

interface SessionState {
  serverId: string;
  ws: WebSocket | null;
  buffer: RingBuffer<ConsoleLine>;
  lastAccess: number;
  pinned: boolean;
  /** Latest known server runtime state ("running", "offline", "starting"...). */
  serverState: string | null;
  /** Last stats payload, parsed. */
  lastStats: unknown;
  /** Promise resolved when the WS becomes ready (auth success received). */
  ready: Promise<void>;
  resolveReady: () => void;
  rejectReady: (err: Error) => void;
  /** Reconnection counter for backoff. */
  reconnectAttempts: number;
  /** Token refresh timer. */
  refreshTimer: NodeJS.Timeout | null;
  /** Whether the session has been explicitly closed by the hub (no auto-reconnect). */
  closed: boolean;
  /** Listeners notified on every new console line, used by runCommand. */
  lineListeners: Set<LineListener>;
  /** Number of active long-lived subscribers (e.g. SSE streams). Prevents reaping. */
  subscriberCount: number;
}

export interface TailOptions {
  /** Maximum number of lines to return (most recent first when limited). */
  limit?: number;
  /** Only return lines whose timestamp is greater than or equal to this epoch ms. */
  sinceMs?: number;
  /** Wait up to this many ms for the WS to become ready before returning. Default 10s. */
  readyTimeoutMs?: number;
  /** Regex string; only lines matching are returned. Applied after limit/since. */
  match?: string;
}

export interface TailResult {
  serverId: string;
  state: string | null;
  bufferedLines: number;
  returnedLines: number;
  lines: ConsoleLine[];
}

export interface RunCommandOptions {
  /** How long to keep collecting output after the command was accepted. Default 1500ms. */
  waitMs?: number;
  /** Regex string; as soon as one captured line matches, return early. */
  expect?: string;
  /** Max time to wait for the WS to be ready before sending. Default 10s. */
  readyTimeoutMs?: number;
}

export interface RunCommandResult {
  serverId: string;
  state: string | null;
  command: string;
  sentAtMs: number;
  elapsedMs: number;
  /** True if the `expect` regex matched and the wait was cut short. */
  matched: boolean;
  /** Lines received in the capture window. */
  lines: ConsoleLine[];
}

export interface WaitOptions {
  /** How long to wait for new output. Default 5000ms. */
  waitMs?: number;
  /** Regex string; as soon as one captured line matches, return early. */
  expect?: string;
  /**
   * Optional epoch-ms timestamp. If provided, buffered lines with `ts >= sinceMs`
   * are included in the result — and if `expect` already matches one of them,
   * the call returns immediately without waiting. Useful to bridge a small gap
   * between e.g. `power_action` and `wait_console` without missing output.
   */
  sinceMs?: number;
  /** Max time to wait for the WS to be ready before starting to listen. Default 10s. */
  readyTimeoutMs?: number;
}

export interface WaitResult {
  serverId: string;
  state: string | null;
  /** True if the `expect` regex matched (either in buffered history or during wait). */
  matched: boolean;
  /** Actual time spent waiting for new lines, excluding the history scan. */
  waitedMs: number;
  /** All lines in the result: optional buffered history + lines received during wait. */
  lines: ConsoleLine[];
}

/**
 * Manages persistent Wings websocket connections for live console buffering.
 *
 * Sessions are lazy: created on the first call referencing a server, kept
 * alive while accessed, and reaped after `consoleIdleTtlMs` of inactivity
 * unless pinned via `watch()`.
 */
export class ConsoleHub {
  private readonly sessions = new Map<string, SessionState>();
  private readonly reaper: NodeJS.Timeout;

  constructor(
    private readonly config: Config,
    private readonly client: PterodactylClient,
  ) {
    // Reap idle sessions every 30s.
    this.reaper = setInterval(() => this.reapIdle(), 30_000);
    this.reaper.unref?.();
  }

  async tail(serverId: string, options: TailOptions = {}): Promise<TailResult> {
    const session = this.ensureSession(serverId);
    session.lastAccess = Date.now();

    const timeout = options.readyTimeoutMs ?? 10_000;
    try {
      await this.waitReady(session, timeout);
    } catch (err) {
      // Return whatever we have even if not yet ready, so callers see context.
      return this.snapshot(session, options, (err as Error).message);
    }

    return this.snapshot(session, options);
  }

  /**
   * Send a command and atomically capture the console output it produces.
   *
   * Registers a line listener before sending so we don't race the WS, then
   * waits up to `waitMs` for new lines (or short-circuits as soon as one
   * matches the `expect` regex). Only lines received during the capture
   * window are returned — callers no longer need to correlate send_command
   * with tail_console timestamps.
   */
  async runCommand(
    serverId: string,
    command: string,
    options: RunCommandOptions = {},
  ): Promise<RunCommandResult> {
    const session = this.ensureSession(serverId);
    session.lastAccess = Date.now();

    await this.waitReady(session, options.readyTimeoutMs ?? 10_000);

    const expectRegex = compileRegex(options.expect, "expect");
    const waitMs = options.waitMs ?? 1500;
    const sentAtMs = Date.now();

    const capture = await this.captureWithListener(
      session,
      waitMs,
      expectRegex,
      () => this.client.sendCommand(serverId, command),
    );

    return {
      serverId,
      state: session.serverState,
      command,
      sentAtMs,
      elapsedMs: Date.now() - sentAtMs,
      matched: capture.matched,
      lines: capture.lines,
    };
  }

  /**
   * Wait for new console output, optionally short-circuiting when a regex matches.
   *
   * Unlike `tail_console` (which reads the rolling buffer once), this tool
   * blocks until either `waitMs` elapses or an `expect` regex matches a new
   * line. Ideal for "restart the server and tell me when it's back up" or
   * "watch for the next crash".
   *
   * If `sinceMs` is provided, already-buffered lines with `ts >= sinceMs`
   * are scanned first — if the `expect` regex matches in that history, the
   * call returns immediately (zero wait). This bridges the small gap between
   * an external action (e.g. power_action HTTP round-trip) and the start of
   * the listening window.
   */
  async waitForOutput(
    serverId: string,
    options: WaitOptions = {},
  ): Promise<WaitResult> {
    const session = this.ensureSession(serverId);
    session.lastAccess = Date.now();

    await this.waitReady(session, options.readyTimeoutMs ?? 10_000);

    const expectRegex = compileRegex(options.expect, "expect");
    const waitMs = options.waitMs ?? 5000;

    // Scan buffered history first if the caller opted in via sinceMs.
    const historical: ConsoleLine[] = [];
    if (options.sinceMs !== undefined) {
      for (const line of session.buffer.toArray()) {
        if (line.ts >= options.sinceMs) historical.push(line);
      }
      if (expectRegex) {
        for (const line of historical) {
          if (expectRegex.test(line.line)) {
            // Already matched in history — no need to wait at all.
            return {
              serverId,
              state: session.serverState,
              matched: true,
              waitedMs: 0,
              lines: historical,
            };
          }
        }
      }
    }

    const capture = await this.captureWithListener(session, waitMs, expectRegex);

    return {
      serverId,
      state: session.serverState,
      matched: capture.matched,
      waitedMs: capture.waitedMs,
      lines: [...historical, ...capture.lines],
    };
  }

  /**
   * Register a line listener, optionally run a side effect (e.g. send a
   * command) before starting the wait window, and collect lines until either
   * the window expires or an expect-regex match short-circuits it.
   */
  private async captureWithListener(
    session: SessionState,
    waitMs: number,
    expectRegex: RegExp | null,
    beforeTimerStart?: () => Promise<unknown>,
  ): Promise<{ lines: ConsoleLine[]; matched: boolean; waitedMs: number }> {
    const collected: ConsoleLine[] = [];
    let matched = false;
    let resolveWait!: () => void;
    const done = new Promise<void>((res) => {
      resolveWait = res;
    });

    const listener: LineListener = (line) => {
      collected.push(line);
      if (expectRegex && !matched && expectRegex.test(line.line)) {
        matched = true;
        resolveWait();
      }
    };
    session.lineListeners.add(listener);

    if (beforeTimerStart) {
      try {
        await beforeTimerStart();
      } catch (err) {
        session.lineListeners.delete(listener);
        throw err;
      }
    }

    const timerStart = Date.now();
    const timer = setTimeout(resolveWait, waitMs);
    try {
      await done;
    } finally {
      clearTimeout(timer);
      session.lineListeners.delete(listener);
      session.lastAccess = Date.now();
    }

    return {
      lines: collected,
      matched,
      waitedMs: Date.now() - timerStart,
    };
  }

  /**
   * Register a long-lived line listener on a server's console session. Used
   * for push-style consumers such as the SSE streaming endpoint.
   *
   * Atomically captures a history snapshot and adds the listener so there is
   * no window where a line could be both missed and duplicated: all lines up
   * to and including the snapshot are in `historicalLines`, all lines after
   * are delivered via the listener. The caller is responsible for invoking
   * `unsubscribe` when done (usually on client disconnect).
   *
   * While at least one subscriber is active, the session is exempt from the
   * idle reaper — callers get a stable stream without needing `watch_server`.
   */
  async subscribe(
    serverId: string,
    listener: LineListener,
    options: { historySinceMs?: number; readyTimeoutMs?: number } = {},
  ): Promise<{ historicalLines: ConsoleLine[]; unsubscribe: () => void }> {
    const session = this.ensureSession(serverId);
    session.lastAccess = Date.now();

    await this.waitReady(session, options.readyTimeoutMs ?? 10_000);

    // Synchronous block: snapshot + listener registration happen in the same
    // microtask, so no line can slip between history and live delivery.
    const historicalLines: ConsoleLine[] =
      options.historySinceMs !== undefined
        ? session.buffer.toArray().filter((l) => l.ts >= options.historySinceMs!)
        : [];
    session.lineListeners.add(listener);
    session.subscriberCount += 1;

    let unsubscribed = false;
    const unsubscribe = () => {
      if (unsubscribed) return;
      unsubscribed = true;
      session.lineListeners.delete(listener);
      session.subscriberCount = Math.max(0, session.subscriberCount - 1);
      session.lastAccess = Date.now();
    };

    return { historicalLines, unsubscribe };
  }

  watch(serverId: string): { serverId: string; pinned: true } {
    const session = this.ensureSession(serverId);
    session.pinned = true;
    session.lastAccess = Date.now();
    return { serverId, pinned: true };
  }

  unwatch(serverId: string): { serverId: string; pinned: false; closed: boolean } {
    const session = this.sessions.get(serverId);
    if (!session) return { serverId, pinned: false, closed: false };
    session.pinned = false;
    return { serverId, pinned: false, closed: false };
  }

  /** Returns a list of currently active sessions. */
  list(): Array<{
    serverId: string;
    pinned: boolean;
    state: string | null;
    bufferedLines: number;
    subscribers: number;
    lastAccessAgoSec: number;
  }> {
    const now = Date.now();
    return [...this.sessions.values()].map((s) => ({
      serverId: s.serverId,
      pinned: s.pinned,
      state: s.serverState,
      bufferedLines: s.buffer.size(),
      subscribers: s.subscriberCount,
      lastAccessAgoSec: Math.round((now - s.lastAccess) / 1000),
    }));
  }

  /** Closes all sessions and stops reaper. Used on graceful shutdown. */
  shutdown(): void {
    clearInterval(this.reaper);
    for (const session of this.sessions.values()) {
      this.closeSession(session);
    }
    this.sessions.clear();
  }

  // ───────────────────────────── internals ─────────────────────────────

  private ensureSession(serverId: string): SessionState {
    let session = this.sessions.get(serverId);
    if (session) return session;

    let resolveReady!: () => void;
    let rejectReady!: (err: Error) => void;
    const ready = new Promise<void>((res, rej) => {
      resolveReady = res;
      rejectReady = rej;
    });
    // Suppress unhandled rejection until someone awaits.
    ready.catch(() => {});

    session = {
      serverId,
      ws: null,
      buffer: createRingBuffer<ConsoleLine>(this.config.consoleBufferSize),
      lastAccess: Date.now(),
      pinned: false,
      serverState: null,
      lastStats: null,
      ready,
      resolveReady,
      rejectReady,
      reconnectAttempts: 0,
      refreshTimer: null,
      closed: false,
      lineListeners: new Set(),
      subscriberCount: 0,
    };
    this.sessions.set(serverId, session);
    void this.connect(session);
    return session;
  }

  private async connect(session: SessionState): Promise<void> {
    if (session.closed) return;

    let creds: WebsocketCredentials;
    try {
      creds = await this.client.getWebsocketCredentials(session.serverId);
    } catch (err) {
      console.error(
        `[console-hub] failed to fetch ws credentials for ${session.serverId}:`,
        err,
      );
      this.scheduleReconnect(session);
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(creds.socket, {
        // Wings checks the Origin header against the panel URL.
        headers: { Origin: this.config.pterodactylUrl },
        perMessageDeflate: true,
      });
    } catch (err) {
      console.error(`[console-hub] ws construction failed for ${session.serverId}:`, err);
      this.scheduleReconnect(session);
      return;
    }

    session.ws = ws;

    ws.on("open", () => {
      // Authenticate immediately.
      ws.send(JSON.stringify({ event: "auth", args: [creds.token] }));
      // Schedule a token refresh ~8 minutes in (tokens last 10 minutes).
      this.scheduleTokenRefresh(session, 8 * 60 * 1000);
    });

    ws.on("message", (raw) => {
      this.handleMessage(session, raw.toString());
    });

    ws.on("error", (err) => {
      console.error(`[console-hub] ws error for ${session.serverId}:`, err.message);
    });

    ws.on("close", (code, reason) => {
      if (session.refreshTimer) {
        clearTimeout(session.refreshTimer);
        session.refreshTimer = null;
      }
      session.ws = null;
      if (session.closed) return;
      console.warn(
        `[console-hub] ws closed for ${session.serverId} (code=${code}, reason=${reason.toString()})`,
      );
      this.scheduleReconnect(session);
    });
  }

  private handleMessage(session: SessionState, raw: string): void {
    let msg: { event?: string; args?: string[] };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    if (!msg.event) return;

    switch (msg.event) {
      case "auth success":
        session.reconnectAttempts = 0;
        // Request the historical log buffer from Wings.
        session.ws?.send(JSON.stringify({ event: "send logs", args: [] }));
        session.ws?.send(JSON.stringify({ event: "send stats", args: [] }));
        session.resolveReady();
        break;

      case "console output":
      case "install output": {
        const lines = msg.args ?? [];
        const now = Date.now();
        for (const line of lines) {
          // Wings sometimes batches multiple lines in one arg separated by \n.
          for (const split of line.split(/\r?\n/)) {
            if (split.length === 0) continue;
            const entry: ConsoleLine = { ts: now, line: split };
            session.buffer.push(entry);
            if (session.lineListeners.size > 0) {
              for (const listener of session.lineListeners) {
                try {
                  listener(entry);
                } catch (err) {
                  console.error(
                    `[console-hub] line listener threw for ${session.serverId}:`,
                    err,
                  );
                }
              }
            }
          }
        }
        break;
      }

      case "status": {
        const state = msg.args?.[0] ?? null;
        session.serverState = state;
        break;
      }

      case "stats": {
        const payload = msg.args?.[0];
        if (payload) {
          try {
            session.lastStats = JSON.parse(payload);
          } catch {
            // ignore malformed
          }
        }
        break;
      }

      case "token expiring":
      case "token expired":
        // Refresh immediately rather than wait for the scheduled timer.
        this.refreshToken(session).catch((err) => {
          console.error(
            `[console-hub] token refresh failed for ${session.serverId}:`,
            err,
          );
        });
        break;

      case "jwt error":
      case "daemon error":
        console.error(
          `[console-hub] ${msg.event} for ${session.serverId}:`,
          msg.args?.join(" "),
        );
        break;
    }
  }

  private scheduleTokenRefresh(session: SessionState, delayMs: number): void {
    if (session.refreshTimer) clearTimeout(session.refreshTimer);
    session.refreshTimer = setTimeout(() => {
      this.refreshToken(session).catch((err) => {
        console.error(
          `[console-hub] scheduled refresh failed for ${session.serverId}:`,
          err,
        );
      });
    }, delayMs);
    session.refreshTimer.unref?.();
  }

  private async refreshToken(session: SessionState): Promise<void> {
    if (session.closed || !session.ws || session.ws.readyState !== WebSocket.OPEN) return;
    const creds = await this.client.getWebsocketCredentials(session.serverId);
    session.ws.send(JSON.stringify({ event: "auth", args: [creds.token] }));
    this.scheduleTokenRefresh(session, 8 * 60 * 1000);
  }

  private scheduleReconnect(session: SessionState): void {
    if (session.closed) return;
    session.reconnectAttempts += 1;
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(5, session.reconnectAttempts - 1));
    setTimeout(() => {
      if (!session.closed) {
        void this.connect(session);
      }
    }, delay).unref?.();
  }

  private waitReady(session: SessionState, timeoutMs: number): Promise<void> {
    if (session.ws?.readyState === WebSocket.OPEN && session.buffer.size() > 0) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`timed out after ${timeoutMs}ms waiting for console ready`));
      }, timeoutMs);
      session.ready.then(
        () => {
          clearTimeout(timer);
          resolve();
        },
        (err) => {
          clearTimeout(timer);
          reject(err);
        },
      );
    });
  }

  private snapshot(
    session: SessionState,
    options: TailOptions,
    warning?: string,
  ): TailResult {
    let lines = session.buffer.toArray();
    if (options.sinceMs !== undefined) {
      lines = lines.filter((l) => l.ts >= options.sinceMs!);
    }
    if (options.limit !== undefined && lines.length > options.limit) {
      lines = lines.slice(-options.limit);
    }
    const matchRegex = compileRegex(options.match, "match");
    if (matchRegex) {
      lines = lines.filter((l) => matchRegex.test(l.line));
    }
    const result: TailResult = {
      serverId: session.serverId,
      state: session.serverState,
      bufferedLines: session.buffer.size(),
      returnedLines: lines.length,
      lines,
    };
    if (warning) {
      (result as TailResult & { warning: string }).warning = warning;
    }
    return result;
  }

  private reapIdle(): void {
    const now = Date.now();
    for (const [id, session] of this.sessions) {
      if (session.pinned || session.subscriberCount > 0) continue;
      if (now - session.lastAccess >= this.config.consoleIdleTtlMs) {
        this.closeSession(session);
        this.sessions.delete(id);
      }
    }
  }

  private closeSession(session: SessionState): void {
    session.closed = true;
    if (session.refreshTimer) {
      clearTimeout(session.refreshTimer);
      session.refreshTimer = null;
    }
    if (session.ws) {
      try {
        session.ws.close(1000, "session reaped");
      } catch {
        // ignore
      }
      session.ws = null;
    }
  }
}
