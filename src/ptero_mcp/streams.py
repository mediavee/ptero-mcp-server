"""SSE endpoint streaming console lines to arbitrary HTTP clients.

Designed for Claude Code's ``Monitor`` tool and other consumers that want
async push notifications instead of explicit polling.

Query params:
- ``match`` (optional) — regex; only matching lines are emitted
- ``include_history_since`` (optional) — epoch-ms; replay buffered lines
  with ``ts >= this``
- ``ready_timeout_ms`` (optional) — max time to wait for the WS to be
  ready; default 10000

Event types:
- ``ready`` — subscription established. Sent once.
- ``line`` — a new matching console line. Payload: ``{ts, line}``.
- ``error`` — something went wrong. Stream ends after.
- ``:keep-alive`` comment every 30s to keep proxies from closing the connection.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from ptero_mcp.console_hub import ConsoleHub, ConsoleLine
from ptero_mcp.logging import get_logger

log = get_logger(__name__)

KEEPALIVE_INTERVAL_S: int = 30


def make_sse_handler(console_hub: ConsoleHub):
    """Build a Starlette route handler for ``GET /streams/{server_id}``."""

    async def handler(request: Request) -> Response:
        server_id = request.path_params.get("server_id")
        if not isinstance(server_id, str) or not server_id:
            return JSONResponse({"error": "Missing serverId path parameter"}, status_code=400)

        match_param = request.query_params.get("match")
        history_since_raw = request.query_params.get("include_history_since")
        ready_timeout_raw = request.query_params.get("ready_timeout_ms")

        match_regex: re.Pattern[str] | None = None
        if match_param:
            try:
                match_regex = re.compile(match_param)
            except re.error as exc:
                return JSONResponse(
                    {"error": f"Invalid match regex: {exc}"}, status_code=400
                )

        history_since_ms: int | None = None
        if history_since_raw is not None:
            try:
                history_since_ms = int(history_since_raw)
            except ValueError:
                return JSONResponse(
                    {"error": "include_history_since must be an integer (epoch ms)"},
                    status_code=400,
                )

        ready_timeout_ms = 10_000
        if ready_timeout_raw is not None:
            try:
                ready_timeout_ms = int(ready_timeout_raw)
            except ValueError:
                return JSONResponse(
                    {"error": "ready_timeout_ms must be an integer"}, status_code=400
                )

        return StreamingResponse(
            _event_stream(
                console_hub,
                server_id,
                match_regex,
                history_since_ms,
                ready_timeout_ms,
                request,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # Prevent nginx (and other reverse proxies) from buffering.
                "X-Accel-Buffering": "no",
            },
        )

    return handler


async def _event_stream(
    console_hub: ConsoleHub,
    server_id: str,
    match_regex: re.Pattern[str] | None,
    history_since_ms: int | None,
    ready_timeout_ms: int,
    request: Request,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def listener(line: ConsoleLine) -> None:
        # The hub fans out from the same event loop as this generator, so
        # put_nowait is safe — the queue is unbounded.
        if match_regex is None or match_regex.search(line.line):
            queue.put_nowait(("line", asdict(line)))

    match_pattern = match_regex.pattern if match_regex is not None else None
    yield _format_event("ready", {"serverId": server_id, "match": match_pattern})

    try:
        sub = await console_hub.subscribe(
            server_id,
            listener,
            history_since_ms=history_since_ms,
            ready_timeout_ms=ready_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        yield _format_event("error", {"message": str(exc)})
        return

    try:
        # Replay history through the same match filter; ordering is preserved
        # because subscribe() captured the snapshot atomically with the listener
        # registration.
        for hist in sub.historical_lines:
            if match_regex is None or match_regex.search(hist.line):
                yield _format_event("line", asdict(hist))

        while True:
            if await request.is_disconnected():
                return
            try:
                event_type, payload = await asyncio.wait_for(
                    queue.get(), timeout=KEEPALIVE_INTERVAL_S
                )
            except TimeoutError:
                yield b": keep-alive\n\n"
                continue
            yield _format_event(event_type, payload)
    finally:
        sub.unsubscribe()


def _format_event(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
