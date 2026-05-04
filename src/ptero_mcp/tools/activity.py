"""Activity log tool."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import map_panel_errors


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="get_activity_log",
        description=(
            "Read panel audit log (power, commands, backups, file edits). Filter by event "
            "prefix. Includes acting user when available."
        ),
    )
    @map_panel_errors
    async def get_activity_log(
        server_id: Annotated[str, Field(description="Server identifier")],
        page: Annotated[int | None, Field(ge=1)] = None,
        per_page: Annotated[int | None, Field(ge=1, le=100)] = None,
        event_filter: Annotated[
            str | None,
            Field(
                description=(
                    "Partial match on event name (e.g. 'power', 'backup', 'console.command')."
                )
            ),
        ] = None,
        sort: Annotated[
            Literal["timestamp", "-timestamp"] | None,
            Field(description="Sort by timestamp asc/desc. Default: panel default (descending)."),
        ] = None,
    ) -> Any:
        return await ctx.client.get_activity_log(
            server_id,
            page=page,
            per_page=per_page,
            event_filter=event_filter,
            sort=sort,
        )
