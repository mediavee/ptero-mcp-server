"""Live console tools backed by ConsoleHub."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ptero_mcp.client import PterodactylError
from ptero_mcp.console_hub import (
    RunCommandOptions,
    TailOptions,
    WaitOptions,
)
from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import (
    IDEMPOTENT,
    READ_ONLY,
    WRITE,
    map_panel_errors,
    ok,
)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="send_command",
        description=(
            "Fire-and-forget console command. Server must be running. "
            "Prefer run_command when you need the output."
        ),
        annotations=WRITE,
    )
    async def send_command(
        server_id: Annotated[str, Field(description="Server identifier")],
        command: Annotated[
            str,
            Field(min_length=1, description="The command to inject into the server console"),
        ],
    ) -> dict[str, Any]:
        try:
            await ctx.client.send_command(server_id, command)
        except PterodactylError as exc:
            if exc.status == 502:
                raise ToolError(
                    f"Cannot send command: server {server_id} is not running (502 from panel)."
                ) from exc
            raise ToolError(f"Pterodactyl API error ({exc.status}): {exc}") from exc
        sent_at_ms = int(time.time() * 1000)
        return ok(
            server_id=server_id,
            command=command,
            sent_at=datetime.now(UTC).isoformat(),
            sent_at_ms=sent_at_ms,
        )

    @mcp.tool(
        name="run_command",
        description=(
            "Send command and capture output atomically. Use `expect` regex to short-circuit "
            "on match. Returns only lines produced during the capture window."
        ),
        annotations=WRITE,
    )
    async def run_command(
        server_id: Annotated[str, Field(description="Server identifier")],
        command: Annotated[
            str,
            Field(min_length=1, description="The command to inject into the server console"),
        ],
        wait_ms: Annotated[
            int | None,
            Field(
                ge=0, le=30_000, description="Output collection time in ms. Default 1500."
            ),
        ] = None,
        expect: Annotated[
            str | None,
            Field(description="Regex; return early when a line matches (matched=true)."),
        ] = None,
        ready_timeout_ms: Annotated[
            int | None,
            Field(ge=100, le=60_000, description="Max WS ready wait in ms. Default 10000."),
        ] = None,
    ) -> dict[str, Any]:
        try:
            result = await ctx.console_hub.run_command(
                server_id,
                command,
                RunCommandOptions(
                    wait_ms=wait_ms or 1500,
                    expect=expect,
                    ready_timeout_ms=ready_timeout_ms or 10_000,
                ),
            )
        except PterodactylError as exc:
            if exc.status == 502:
                raise ToolError(
                    f"Cannot send command: server {server_id} is not running (502 from panel)."
                ) from exc
            raise ToolError(f"Pterodactyl API error ({exc.status}): {exc}") from exc

        return _result_to_dict(result)

    @mcp.tool(
        name="tail_console",
        description=(
            "Read recent lines from the console ring buffer. Use `match` for regex filtering. "
            "First call opens a persistent WS; idle sessions are reaped after ~10 min."
        ),
        annotations=READ_ONLY,
    )
    async def tail_console(
        server_id: Annotated[str, Field(description="Server identifier")],
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=10_000,
                description="Max lines to return (most recent). Default: 100.",
            ),
        ] = 100,
        since_ms: Annotated[
            int | None,
            Field(description="Only return lines newer than this epoch-ms."),
        ] = None,
        match: Annotated[
            str | None,
            Field(description="Regex filter; only matching lines are returned."),
        ] = None,
        ready_timeout_ms: Annotated[
            int | None,
            Field(ge=100, le=60_000, description="Max WS ready wait in ms. Default 10000."),
        ] = None,
    ) -> dict[str, Any]:
        try:
            result = await ctx.console_hub.tail(
                server_id,
                TailOptions(
                    limit=limit,
                    since_ms=since_ms,
                    match=match,
                    ready_timeout_ms=ready_timeout_ms or 10_000,
                ),
            )
        except ValueError as exc:  # invalid regex
            raise ToolError(str(exc)) from exc
        return _result_to_dict(result)

    @mcp.tool(
        name="wait_console",
        description=(
            "Block until new output arrives, `wait_ms` elapses, or `expect` regex matches. "
            "Use `since_ms` to include buffered history. Prefer over polling tail_console."
        ),
        annotations=READ_ONLY,
    )
    async def wait_console(
        server_id: Annotated[str, Field(description="Server identifier")],
        wait_ms: Annotated[
            int | None,
            Field(
                ge=0,
                le=120_000,
                description="Wait time in ms. Default 5000. Max 120000.",
            ),
        ] = None,
        expect: Annotated[
            str | None,
            Field(description="Regex; return early when a line matches (matched=true)."),
        ] = None,
        since_ms: Annotated[
            int | None,
            Field(
                description="Include buffered lines with ts >= since_ms and scan them for expect."
            ),
        ] = None,
        ready_timeout_ms: Annotated[
            int | None,
            Field(ge=100, le=60_000, description="Max WS ready wait in ms. Default 10000."),
        ] = None,
    ) -> dict[str, Any]:
        try:
            result = await ctx.console_hub.wait_for_output(
                server_id,
                WaitOptions(
                    wait_ms=wait_ms if wait_ms is not None else 5000,
                    expect=expect,
                    since_ms=since_ms,
                    ready_timeout_ms=ready_timeout_ms or 10_000,
                ),
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return _result_to_dict(result)

    @mcp.tool(
        name="watch_server",
        description="Pin a console session to prevent idle reaping. Call unwatch_server to release.",
        annotations=IDEMPOTENT,
    )
    @map_panel_errors
    async def watch_server(
        server_id: Annotated[str, Field(description="Server identifier")],
    ) -> dict[str, Any]:
        return await ctx.console_hub.watch(server_id)

    @mcp.tool(
        name="unwatch_server",
        description="Remove pin from watch_server. Session reaped after normal idle TTL.",
        annotations=IDEMPOTENT,
    )
    async def unwatch_server(
        server_id: Annotated[str, Field(description="Server identifier")],
    ) -> dict[str, Any]:
        return ctx.console_hub.unwatch(server_id)

    @mcp.tool(
        name="list_console_sessions",
        description="List active console sessions with state, buffer size, and pin status.",
        annotations=READ_ONLY,
    )
    async def list_console_sessions() -> dict[str, Any]:
        return {"sessions": [asdict(s) for s in ctx.console_hub.list_sessions()]}


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize a hub result dataclass, converting nested ConsoleLine entries."""
    data = asdict(result)
    if "warning" in data and data["warning"] is None:
        data.pop("warning")
    return data
