"""Discovery & state tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import READ_ONLY, map_panel_errors


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="list_servers",
        description=(
            "List all servers with identifier, name, node, status, and limits. Paginated."
        ),
        annotations=READ_ONLY,
    )
    @map_panel_errors
    async def list_servers(
        page: Annotated[int | None, Field(description="Page number (1-indexed)", ge=1)] = None,
        per_page: Annotated[
            int | None, Field(description="Items per page (max 100)", ge=1, le=100)
        ] = None,
    ) -> Any:
        return await ctx.client.list_servers(page=page, per_page=per_page)

    @mcp.tool(
        name="get_server",
        description=(
            "Get full server details: limits, feature limits, startup, docker image, status."
        ),
        annotations=READ_ONLY,
    )
    @map_panel_errors
    async def get_server(
        server_id: Annotated[
            str,
            Field(
                description=(
                    "Server identifier (short UUID, visible in panel URLs and list_servers output)"
                ),
            ),
        ],
    ) -> Any:
        return await ctx.client.get_server(server_id)

    @mcp.tool(
        name="get_resources",
        description=(
            "Get current resource utilization: state, memory, CPU, disk, network, uptime."
        ),
        annotations=READ_ONLY,
    )
    @map_panel_errors
    async def get_resources(
        server_id: Annotated[str, Field(description="Server identifier")],
    ) -> Any:
        return await ctx.client.get_resources(server_id)
