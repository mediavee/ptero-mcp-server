"""Persistent Wings websocket buffer with atomic listener semantics.

Sessions are lazy: created on the first call referencing a server, kept alive
while accessed, and reaped after the configured idle TTL unless pinned via
``watch()`` or holding active ``subscribe()`` listeners.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from ptero_mcp.client import (
    MissingCredentialsError,
    PterodactylClient,
    current_api_key,
    current_panel_url,
    use_credentials,
)
from ptero_mcp.config import Settings
from ptero_mcp.logging import get_logger

log = get_logger(__name__)


SessionKey = tuple[str, str, str]
"""(panel_url, api_key, server_id) — strict isolation per operator and panel."""


@dataclass(slots=True, frozen=True)
class ConsoleLine:
    """A single console line as captured by the hub."""

    ts: int
    """Epoch milliseconds when the line was received by the hub."""

    line: str
    """Raw line text (may contain ANSI escapes)."""


LineListener = Callable[[ConsoleLine], None]


@dataclass(slots=True)
class TailOptions:
    limit: int | None = None
    since_ms: int | None = None
    ready_timeout_ms: int = 10_000
    match: str | None = None


@dataclass(slots=True)
class TailResult:
    server_id: str
    state: str | None
    buffered_lines: int
    returned_lines: int
    lines: list[ConsoleLine]
    warning: str | None = None


@dataclass(slots=True)
class RunCommandOptions:
    wait_ms: int = 1500
    expect: str | None = None
    ready_timeout_ms: int = 10_000


@dataclass(slots=True)
class RunCommandResult:
    server_id: str
    state: str | None
    command: str
    sent_at_ms: int
    elapsed_ms: int
    matched: bool
    lines: list[ConsoleLine]


@dataclass(slots=True)
class WaitOptions:
    wait_ms: int = 5000
    expect: str | None = None
    since_ms: int | None = None
    ready_timeout_ms: int = 10_000


@dataclass(slots=True)
class WaitResult:
    server_id: str
    state: str | None
    matched: bool
    waited_ms: int
    lines: list[ConsoleLine]


@dataclass(slots=True)
class SubscribeResult:
    historical_lines: list[ConsoleLine]
    unsubscribe: Callable[[], None]


@dataclass(slots=True)
class SessionInfo:
    server_id: str
    pinned: bool
    state: str | None
    buffered_lines: int
    subscribers: int
    last_access_ago_sec: int


def _compile_regex(pattern: str | None, label: str) -> re.Pattern[str] | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f'Invalid {label} regex "{pattern}": {exc}') from exc


@dataclass(slots=True)
class _Session:
    server_id: str
    panel_url: str
    api_key: str
    buffer: deque[ConsoleLine]
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    last_access: float = field(default_factory=time.monotonic)
    pinned: bool = False
    server_state: str | None = None
    last_stats: Any = None
    closed: bool = False
    line_listeners: set[LineListener] = field(default_factory=set)
    subscriber_count: int = 0
    ws: ClientConnection | None = None
    runner_task: asyncio.Task[None] | None = None
    refresh_task: asyncio.Task[None] | None = None
    # Strong refs for fire-and-forget tasks. Without this set, asyncio.create_task
    # results would be only weakly held by the loop (3.11.1+) and could be GC'd
    # mid-flight — silently losing auth/refresh/log-pull WS sends.
    bg_tasks: set[asyncio.Task[None]] = field(default_factory=set)


class ConsoleHub:
    """Manages persistent Wings websocket connections for live console buffering."""

    REAPER_INTERVAL_S: int = 30
    TOKEN_REFRESH_INTERVAL_S: int = 8 * 60

    def __init__(self, settings: Settings, client: PterodactylClient) -> None:
        self._settings = settings
        self._client = client
        self._sessions: dict[SessionKey, _Session] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        self._sessions_lock = asyncio.Lock()
        # Strong refs for fire-and-forget close tasks fired by the reaper.
        self._close_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _spawn(session: _Session, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """Schedule a fire-and-forget task and keep a strong ref on the session.

        Without this set, ``asyncio.create_task`` results would be only weakly
        referenced by the loop (CPython 3.11.1+) and could be GC'd mid-flight.
        """
        task = asyncio.create_task(coro)
        session.bg_tasks.add(task)
        task.add_done_callback(session.bg_tasks.discard)
        return task

    @staticmethod
    def _current_creds() -> tuple[str, str]:
        panel_url = current_panel_url.get()
        api_key = current_api_key.get()
        if not panel_url or not api_key:
            raise MissingCredentialsError(
                "No Pterodactyl panel URL / API key in request context — "
                "the HTTP middleware should have rejected this request earlier."
            )
        return panel_url, api_key

    # ─────────────────────────────── lifecycle ───────────────────────────────

    async def start(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop(), name="console-reaper")

    async def shutdown(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

        await asyncio.gather(
            *(self._close_session(s) for s in list(self._sessions.values())),
            return_exceptions=True,
        )
        self._sessions.clear()

    # ─────────────────────────────── public API ──────────────────────────────

    async def tail(self, server_id: str, options: TailOptions) -> TailResult:
        session = await self._ensure_session(server_id)
        session.last_access = time.monotonic()

        warning: str | None = None
        try:
            await asyncio.wait_for(session.ready.wait(), timeout=options.ready_timeout_ms / 1000)
        except TimeoutError:
            warning = f"timed out after {options.ready_timeout_ms}ms waiting for console ready"

        return self._snapshot(session, options, warning)

    async def run_command(
        self, server_id: str, command: str, options: RunCommandOptions
    ) -> RunCommandResult:
        session = await self._ensure_session(server_id)
        session.last_access = time.monotonic()
        await asyncio.wait_for(session.ready.wait(), timeout=options.ready_timeout_ms / 1000)

        expect_re = _compile_regex(options.expect, "expect")
        sent_at_ms = _now_ms()

        async def send_command() -> None:
            await self._client.send_command(server_id, command)

        capture = await self._capture_with_listener(
            session, options.wait_ms, expect_re, before_timer_start=send_command
        )

        return RunCommandResult(
            server_id=server_id,
            state=session.server_state,
            command=command,
            sent_at_ms=sent_at_ms,
            elapsed_ms=_now_ms() - sent_at_ms,
            matched=capture.matched,
            lines=capture.lines,
        )

    async def wait_for_output(self, server_id: str, options: WaitOptions) -> WaitResult:
        session = await self._ensure_session(server_id)
        session.last_access = time.monotonic()
        await asyncio.wait_for(session.ready.wait(), timeout=options.ready_timeout_ms / 1000)

        expect_re = _compile_regex(options.expect, "expect")

        historical: list[ConsoleLine] = []
        if options.since_ms is not None:
            historical = [ln for ln in session.buffer if ln.ts >= options.since_ms]
            if expect_re is not None:
                for ln in historical:
                    if expect_re.search(ln.line):
                        return WaitResult(
                            server_id=server_id,
                            state=session.server_state,
                            matched=True,
                            waited_ms=0,
                            lines=historical,
                        )

        capture = await self._capture_with_listener(session, options.wait_ms, expect_re)

        return WaitResult(
            server_id=server_id,
            state=session.server_state,
            matched=capture.matched,
            waited_ms=capture.waited_ms,
            lines=[*historical, *capture.lines],
        )

    async def subscribe(
        self,
        server_id: str,
        listener: LineListener,
        *,
        history_since_ms: int | None = None,
        ready_timeout_ms: int = 10_000,
    ) -> SubscribeResult:
        """Register a long-lived listener and snapshot history atomically.

        While at least one subscriber is active, the session is exempt from the
        idle reaper. The caller must invoke ``unsubscribe`` when done.
        """

        session = await self._ensure_session(server_id)
        session.last_access = time.monotonic()
        await asyncio.wait_for(session.ready.wait(), timeout=ready_timeout_ms / 1000)

        if history_since_ms is not None:
            historical = [ln for ln in session.buffer if ln.ts >= history_since_ms]
        else:
            historical = []

        session.line_listeners.add(listener)
        session.subscriber_count += 1

        unsubscribed = False

        def unsubscribe() -> None:
            nonlocal unsubscribed
            if unsubscribed:
                return
            unsubscribed = True
            session.line_listeners.discard(listener)
            session.subscriber_count = max(0, session.subscriber_count - 1)
            session.last_access = time.monotonic()

        return SubscribeResult(historical_lines=historical, unsubscribe=unsubscribe)

    async def watch(self, server_id: str) -> dict[str, Any]:
        session = await self._ensure_session(server_id)
        session.pinned = True
        session.last_access = time.monotonic()
        return {"server_id": server_id, "pinned": True}

    def unwatch(self, server_id: str) -> dict[str, Any]:
        try:
            panel_url, api_key = self._current_creds()
        except MissingCredentialsError:
            return {"server_id": server_id, "pinned": False, "closed": False}
        session = self._sessions.get((panel_url, api_key, server_id))
        if session is None:
            return {"server_id": server_id, "pinned": False, "closed": False}
        session.pinned = False
        return {"server_id": server_id, "pinned": False, "closed": False}

    def list_sessions(self) -> list[SessionInfo]:
        """List sessions visible to the current request's credentials.

        Each operator key only sees its own sessions — never another operator's
        — even on the same panel.
        """
        try:
            panel_url, api_key = self._current_creds()
        except MissingCredentialsError:
            return []
        now = time.monotonic()
        return [
            SessionInfo(
                server_id=s.server_id,
                pinned=s.pinned,
                state=s.server_state,
                buffered_lines=len(s.buffer),
                subscribers=s.subscriber_count,
                last_access_ago_sec=int(now - s.last_access),
            )
            for (p, k, _), s in self._sessions.items()
            if p == panel_url and k == api_key
        ]

    # ─────────────────────────────── internals ───────────────────────────────

    async def _ensure_session(self, server_id: str) -> _Session:
        panel_url, api_key = self._current_creds()
        key: SessionKey = (panel_url, api_key, server_id)
        async with self._sessions_lock:
            session = self._sessions.get(key)
            if session is not None:
                return session

            session = _Session(
                server_id=server_id,
                panel_url=panel_url,
                api_key=api_key,
                buffer=deque(maxlen=self._settings.console_buffer_size),
            )
            self._sessions[key] = session
            session.runner_task = asyncio.create_task(
                self._run_session(session), name=f"console-{server_id}"
            )
            return session

    async def _run_session(self, session: _Session) -> None:
        """Connect/reconnect loop with exponential backoff.

        Wraps the body in ``use_credentials`` so all client calls in the
        background task chain (``get_websocket_credentials``, refresh) target
        the right panel + key without needing a live request task on the stack.
        """

        attempt = 0
        with use_credentials(session.panel_url, session.api_key):
            while not session.closed:
                try:
                    await self._connect_and_pump(session)
                    attempt = 0  # reset after a clean disconnect
                except Exception as exc:
                    log.warning(
                        "console_session_disconnect",
                        server_id=session.server_id,
                        panel_url=session.panel_url,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

                if session.closed:
                    return

                attempt += 1
                delay = min(30.0, 1.0 * (2 ** min(5, attempt - 1)))
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return

    async def _connect_and_pump(self, session: _Session) -> None:
        creds = await self._client.get_websocket_credentials(session.server_id)

        # Wings checks the Origin header against the panel URL.
        async with websockets.connect(
            creds.socket,
            origin=session.panel_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=2**20,
        ) as ws:
            session.ws = ws
            try:
                await ws.send(json.dumps({"event": "auth", "args": [creds.token]}))

                # Schedule a refresh before the 10-min token expiry.
                if session.refresh_task is not None:
                    session.refresh_task.cancel()
                session.refresh_task = asyncio.create_task(
                    self._refresh_loop(session), name=f"console-refresh-{session.server_id}"
                )

                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    self._handle_message(session, raw)
            finally:
                if session.refresh_task is not None:
                    session.refresh_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await session.refresh_task
                    session.refresh_task = None
                session.ws = None

    async def _refresh_loop(self, session: _Session) -> None:
        try:
            while not session.closed:
                await asyncio.sleep(self.TOKEN_REFRESH_INTERVAL_S)
                if session.ws is None or session.closed:
                    return
                try:
                    creds = await self._client.get_websocket_credentials(session.server_id)
                    await session.ws.send(json.dumps({"event": "auth", "args": [creds.token]}))
                except Exception as exc:
                    log.error(
                        "token_refresh_failed",
                        server_id=session.server_id,
                        error=str(exc),
                    )
        except asyncio.CancelledError:
            raise

    def _handle_message(self, session: _Session, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        event = msg.get("event")
        if not event:
            return
        args: list[Any] = msg.get("args") or []

        if event == "auth success":
            # Pull recent history and a stats snapshot.
            ws = session.ws
            if ws is not None:
                self._spawn(
                    session, ws.send(json.dumps({"event": "send logs", "args": []}))
                )
                self._spawn(
                    session, ws.send(json.dumps({"event": "send stats", "args": []}))
                )
            session.ready.set()

        elif event in ("console output", "install output"):
            now = _now_ms()
            for chunk in args:
                # Wings sometimes batches multiple lines in one arg separated by \n.
                for split in re.split(r"\r?\n", str(chunk)):
                    if not split:
                        continue
                    entry = ConsoleLine(ts=now, line=split)
                    session.buffer.append(entry)
                    if session.line_listeners:
                        self._fanout(session, entry)

        elif event == "status":
            session.server_state = args[0] if args else None

        elif event == "stats":
            payload = args[0] if args else None
            if isinstance(payload, str):
                with contextlib.suppress(json.JSONDecodeError):
                    session.last_stats = json.loads(payload)

        elif event in ("token expiring", "token expired"):
            self._spawn(session, self._refresh_now(session))

        elif event in ("jwt error", "daemon error"):
            log.error(
                "wings_event_error",
                server_id=session.server_id,
                event=event,
                args=args,
            )

    async def _refresh_now(self, session: _Session) -> None:
        if session.ws is None or session.closed:
            return
        try:
            creds = await self._client.get_websocket_credentials(session.server_id)
            await session.ws.send(json.dumps({"event": "auth", "args": [creds.token]}))
        except Exception as exc:
            log.error("token_refresh_failed", server_id=session.server_id, error=str(exc))

    def _fanout(self, session: _Session, entry: ConsoleLine) -> None:
        # Iterate over a snapshot so a listener that unsubscribes during fan-out
        # doesn't mutate the set we're iterating.
        for listener in list(session.line_listeners):
            try:
                listener(entry)
            except Exception as exc:
                log.error(
                    "line_listener_error",
                    server_id=session.server_id,
                    error=str(exc),
                )

    async def _capture_with_listener(
        self,
        session: _Session,
        wait_ms: int,
        expect_re: re.Pattern[str] | None,
        before_timer_start: Callable[[], Awaitable[Any]] | None = None,
    ) -> _Capture:
        loop = asyncio.get_running_loop()
        collected: list[ConsoleLine] = []
        matched = False
        done: asyncio.Future[None] = loop.create_future()

        def listener(entry: ConsoleLine) -> None:
            nonlocal matched
            collected.append(entry)
            if expect_re is not None and not matched and expect_re.search(entry.line):
                matched = True
                if not done.done():
                    done.set_result(None)

        session.line_listeners.add(listener)
        timer_start = time.monotonic()
        try:
            if before_timer_start is not None:
                await before_timer_start()
                # Reset so the elapsed window only counts the listening phase.
                timer_start = time.monotonic()

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done, timeout=wait_ms / 1000)
        finally:
            session.line_listeners.discard(listener)
            session.last_access = time.monotonic()

        return _Capture(
            lines=collected,
            matched=matched,
            waited_ms=int((time.monotonic() - timer_start) * 1000),
        )

    def _snapshot(
        self,
        session: _Session,
        options: TailOptions,
        warning: str | None = None,
    ) -> TailResult:
        lines: list[ConsoleLine] = list(session.buffer)
        if options.since_ms is not None:
            lines = [ln for ln in lines if ln.ts >= options.since_ms]

        if options.limit is not None and len(lines) > options.limit:
            lines = lines[-options.limit :]

        match_re = _compile_regex(options.match, "match")
        if match_re is not None:
            lines = [ln for ln in lines if match_re.search(ln.line)]

        return TailResult(
            server_id=session.server_id,
            state=session.server_state,
            buffered_lines=len(session.buffer),
            returned_lines=len(lines),
            lines=lines,
            warning=warning,
        )

    async def _reaper_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.REAPER_INTERVAL_S)
                self._reap_idle()
        except asyncio.CancelledError:
            return

    def _reap_idle(self) -> None:
        now = time.monotonic()
        idle_threshold_s = self._settings.console_idle_ttl
        to_close: list[tuple[SessionKey, _Session]] = []
        for key, session in list(self._sessions.items()):
            if session.pinned or session.subscriber_count > 0:
                continue
            if (now - session.last_access) >= idle_threshold_s:
                to_close.append((key, session))

        for key, session in to_close:
            self._sessions.pop(key, None)
            task = asyncio.create_task(self._close_session(session))
            self._close_tasks.add(task)
            task.add_done_callback(self._close_tasks.discard)

    async def _close_session(self, session: _Session) -> None:
        session.closed = True
        if session.refresh_task is not None:
            session.refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.refresh_task
            session.refresh_task = None
        if session.ws is not None:
            with contextlib.suppress(Exception):
                await session.ws.close(code=1000, reason="session reaped")
            session.ws = None
        if session.runner_task is not None:
            session.runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.runner_task
            session.runner_task = None


@dataclass(slots=True)
class _Capture:
    lines: list[ConsoleLine]
    matched: bool
    waited_ms: int


def _now_ms() -> int:
    return int(time.time() * 1000)
