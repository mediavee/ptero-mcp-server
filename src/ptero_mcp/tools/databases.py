"""Database tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import map_panel_errors, ok


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="list_databases",
        description="List databases with name, host, port, and password.",
    )
    @map_panel_errors
    async def list_databases(
        server_id: Annotated[str, Field(description="Server identifier")],
    ) -> Any:
        return await ctx.client.list_databases(server_id)

    @mcp.tool(
        name="create_database",
        description="Create a new database. Requires available slots (see get_server feature_limits).",
    )
    @map_panel_errors
    async def create_database(
        server_id: Annotated[str, Field(description="Server identifier")],
        database: Annotated[
            str,
            Field(
                min_length=1,
                max_length=48,
                description="Database name (will be prefixed by the panel, e.g. s5_<name>)",
            ),
        ],
        remote: Annotated[
            str,
            Field(
                description=(
                    "Allowed remote host pattern (e.g. '%' for any, '192.168.%' for a subnet, "
                    "'127.0.0.1' for local-only)"
                )
            ),
        ],
    ) -> Any:
        return await ctx.client.create_database(server_id, database, remote)

    @mcp.tool(
        name="rotate_database_password",
        description="Generate a new random password for a database. The new password is returned in the response.",
    )
    @map_panel_errors
    async def rotate_database_password(
        server_id: Annotated[str, Field(description="Server identifier")],
        database_id: Annotated[
            str, Field(description="Database identifier (hashid from list_databases)")
        ],
    ) -> Any:
        return await ctx.client.rotate_database_password(server_id, database_id)

    @mcp.tool(
        name="delete_database",
        description="Delete a database. DESTRUCTIVE — the data is gone.",
    )
    @map_panel_errors
    async def delete_database(
        server_id: Annotated[str, Field(description="Server identifier")],
        database_id: Annotated[str, Field(description="Database identifier (hashid)")],
    ) -> dict[str, Any]:
        await ctx.client.delete_database(server_id, database_id)
        return ok(server_id=server_id, database_id=database_id, deleted=True)
