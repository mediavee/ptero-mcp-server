"""Schedule + task tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from ptero_mcp.context import ToolContext
from ptero_mcp.tools._common import map_panel_errors, ok

CronField = Annotated[
    str, Field(description="Cron field value, e.g. '*', '*/5', '0', '1,15'")
]
ScheduleId = Annotated[int, Field(ge=1)]
TaskId = Annotated[int, Field(ge=1)]


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(
        name="list_schedules",
        description="List all schedules with their tasks.",
    )
    @map_panel_errors
    async def list_schedules(
        server_id: Annotated[str, Field(description="Server identifier")],
    ) -> Any:
        return await ctx.client.list_schedules(server_id)

    @mcp.tool(
        name="get_schedule",
        description="Get details of a single schedule by id, including its tasks.",
    )
    @map_panel_errors
    async def get_schedule(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
    ) -> Any:
        return await ctx.client.get_schedule(server_id, schedule_id)

    @mcp.tool(
        name="create_schedule",
        description="Create a cron schedule. Add tasks via create_schedule_task after creation.",
    )
    @map_panel_errors
    async def create_schedule(
        server_id: Annotated[str, Field(description="Server identifier")],
        name: Annotated[str, Field(min_length=1, max_length=191)],
        minute: CronField,
        hour: CronField,
        day_of_month: CronField,
        month: CronField,
        day_of_week: CronField,
        is_active: Annotated[bool | None, Field(description="Default true")] = None,
        only_when_online: Annotated[
            bool | None,
            Field(description="Skip execution when server is not running. Default false."),
        ] = None,
    ) -> Any:
        return await ctx.client.create_schedule(
            server_id,
            name=name,
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month=month,
            day_of_week=day_of_week,
            is_active=True if is_active is None else is_active,
            only_when_online=bool(only_when_online),
        )

    @mcp.tool(
        name="update_schedule",
        description="Update an existing schedule. All fields are required (panel uses POST as full replace).",
    )
    @map_panel_errors
    async def update_schedule(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
        name: Annotated[str, Field(min_length=1, max_length=191)],
        is_active: bool,
        minute: CronField,
        hour: CronField,
        day_of_month: CronField,
        month: CronField,
        day_of_week: CronField,
        only_when_online: bool | None = None,
    ) -> Any:
        return await ctx.client.update_schedule(
            server_id,
            schedule_id,
            name=name,
            is_active=is_active,
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month=month,
            day_of_week=day_of_week,
            only_when_online=bool(only_when_online),
        )

    @mcp.tool(
        name="delete_schedule",
        description="Delete a schedule and all its tasks.",
    )
    @map_panel_errors
    async def delete_schedule(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
    ) -> dict[str, Any]:
        await ctx.client.delete_schedule(server_id, schedule_id)
        return ok(server_id=server_id, schedule_id=schedule_id, deleted=True)

    @mcp.tool(
        name="execute_schedule",
        description="Execute a schedule immediately, ignoring cron and active flag.",
    )
    @map_panel_errors
    async def execute_schedule(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
    ) -> dict[str, Any]:
        await ctx.client.execute_schedule(server_id, schedule_id)
        return ok(
            server_id=server_id,
            schedule_id=schedule_id,
            triggered_at=datetime.now(UTC).isoformat(),
        )

    @mcp.tool(
        name="create_schedule_task",
        description=(
            "Add a task to a schedule. Types: command (console), power "
            "(start/stop/restart/kill), backup. Sequential execution with time_offset delay."
        ),
    )
    @map_panel_errors
    async def create_schedule_task(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
        action: Literal["command", "power", "backup"],
        time_offset: Annotated[
            int, Field(ge=0, le=900, description="Seconds to wait before firing (0-900)")
        ],
        payload: Annotated[
            str | None,
            Field(
                description=(
                    "Required for command/power; optional for backup (ignored files list)"
                )
            ),
        ] = None,
        sequence_id: Annotated[
            int | None,
            Field(
                ge=1,
                description="Position in the task sequence (1-based). Defaults to last.",
            ),
        ] = None,
        continue_on_failure: Annotated[bool | None, Field(description="Default false")] = None,
    ) -> Any:
        return await ctx.client.create_schedule_task(
            server_id,
            schedule_id,
            action=action,
            time_offset=time_offset,
            payload=payload,
            sequence_id=sequence_id,
            continue_on_failure=bool(continue_on_failure),
        )

    @mcp.tool(
        name="update_schedule_task",
        description="Update an existing task on a schedule. All fields are required.",
    )
    @map_panel_errors
    async def update_schedule_task(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
        task_id: TaskId,
        action: Literal["command", "power", "backup"],
        time_offset: Annotated[int, Field(ge=0, le=900)],
        payload: str | None = None,
        sequence_id: Annotated[int | None, Field(ge=1)] = None,
        continue_on_failure: bool | None = None,
    ) -> Any:
        return await ctx.client.update_schedule_task(
            server_id,
            schedule_id,
            task_id,
            action=action,
            time_offset=time_offset,
            payload=payload,
            sequence_id=sequence_id,
            continue_on_failure=bool(continue_on_failure),
        )

    @mcp.tool(
        name="delete_schedule_task",
        description="Delete a task from a schedule.",
    )
    @map_panel_errors
    async def delete_schedule_task(
        server_id: Annotated[str, Field(description="Server identifier")],
        schedule_id: ScheduleId,
        task_id: TaskId,
    ) -> dict[str, Any]:
        await ctx.client.delete_schedule_task(server_id, schedule_id, task_id)
        return ok(
            server_id=server_id,
            schedule_id=schedule_id,
            task_id=task_id,
            deleted=True,
        )
