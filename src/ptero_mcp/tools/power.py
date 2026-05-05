"""Power management tool."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import DESTRUCTIVE, map_panel_errors, ok


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="power_action",
        description=(
            "Send power signal (start/stop/restart/kill). Async — returns `sent_at_ms` "
            "for use with wait_console `since_ms`. The 'kill' signal sends SIGKILL "
            "and risks data loss on unsaved state."
        ),
        annotations=DESTRUCTIVE,
    )
    @map_panel_errors
    async def power_action(
        server_id: Annotated[str, Field(description="Server identifier")],
        signal: Annotated[
            Literal["start", "stop", "restart", "kill"],
            Field(description="Power signal to send"),
        ],
    ) -> dict[str, Any]:
        await ctx.client.send_power(server_id, signal)
        sent_at_ms = int(time.time() * 1000)
        return ok(
            server_id=server_id,
            signal=signal,
            sent_at=datetime.now(UTC).isoformat(),
            sent_at_ms=sent_at_ms,
        )
