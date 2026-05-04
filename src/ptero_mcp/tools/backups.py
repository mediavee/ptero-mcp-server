"""Backup tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import map_panel_errors, ok


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="list_backups",
        description="List backups with uuid, name, size, status, and lock state. Paginated.",
    )
    @map_panel_errors
    async def list_backups(
        server_id: Annotated[str, Field(description="Server identifier")],
        page: Annotated[int | None, Field(ge=1)] = None,
        per_page: Annotated[int | None, Field(ge=1, le=50)] = None,
    ) -> Any:
        return await ctx.client.list_backups(server_id, page=page, per_page=per_page)

    @mcp.tool(
        name="get_backup",
        description="Get details of a single backup by its uuid.",
    )
    @map_panel_errors
    async def get_backup(
        server_id: Annotated[str, Field(description="Server identifier")],
        backup_uuid: Annotated[str, Field(description="Backup UUID")],
    ) -> Any:
        return await ctx.client.get_backup(server_id, backup_uuid)

    @mcp.tool(
        name="create_backup",
        description="Create a backup (async). Poll get_backup for completion.",
    )
    @map_panel_errors
    async def create_backup(
        server_id: Annotated[str, Field(description="Server identifier")],
        name: Annotated[
            str | None, Field(max_length=191, description="Optional human-readable name")
        ] = None,
        ignored: Annotated[
            str | None,
            Field(
                description=(
                    "Newline-separated list of glob patterns to exclude from the backup "
                    "(matches .pteroignore syntax)"
                )
            ),
        ] = None,
        is_locked: Annotated[
            bool | None,
            Field(description="If true, prevent deletion until explicitly unlocked"),
        ] = None,
    ) -> Any:
        return await ctx.client.create_backup(
            server_id, name=name, ignored=ignored, is_locked=is_locked
        )

    @mcp.tool(
        name="delete_backup",
        description="Delete a backup. Fails if the backup is locked — call `toggle_backup_lock` first.",
    )
    @map_panel_errors
    async def delete_backup(
        server_id: Annotated[str, Field(description="Server identifier")],
        backup_uuid: Annotated[str, Field(description="Backup UUID")],
    ) -> dict[str, Any]:
        await ctx.client.delete_backup(server_id, backup_uuid)
        return ok(server_id=server_id, backup_uuid=backup_uuid, deleted=True)

    @mcp.tool(
        name="toggle_backup_lock",
        description="Toggle the lock state of a backup. Locked backups cannot be deleted.",
    )
    @map_panel_errors
    async def toggle_backup_lock(
        server_id: Annotated[str, Field(description="Server identifier")],
        backup_uuid: Annotated[str, Field(description="Backup UUID")],
    ) -> Any:
        return await ctx.client.toggle_backup_lock(server_id, backup_uuid)

    @mcp.tool(
        name="restore_backup",
        description="Restore backup over server files. DESTRUCTIVE if truncate=true (wipes first).",
    )
    @map_panel_errors
    async def restore_backup(
        server_id: Annotated[str, Field(description="Server identifier")],
        backup_uuid: Annotated[str, Field(description="Backup UUID")],
        truncate: Annotated[
            bool | None,
            Field(description="Wipe existing server files before restoring. Default: false."),
        ] = None,
    ) -> dict[str, Any]:
        await ctx.client.restore_backup(server_id, backup_uuid, truncate=bool(truncate))
        return ok(
            server_id=server_id,
            backup_uuid=backup_uuid,
            truncate=bool(truncate),
            started_at=datetime.now(UTC).isoformat(),
        )

    @mcp.tool(
        name="get_backup_download_url",
        description="Get a signed, time-limited download URL for a backup.",
    )
    @map_panel_errors
    async def get_backup_download_url(
        server_id: Annotated[str, Field(description="Server identifier")],
        backup_uuid: Annotated[str, Field(description="Backup UUID")],
    ) -> Any:
        return await ctx.client.get_backup_download_url(server_id, backup_uuid)
