"""Tool registration entry point."""

from __future__ import annotations

from fastmcp import FastMCP

from ptero_mcp.context import ToolContext
from ptero_mcp.tools.activity import register as register_activity
from ptero_mcp.tools.backups import register as register_backups
from ptero_mcp.tools.console import register as register_console
from ptero_mcp.tools.databases import register as register_databases
from ptero_mcp.tools.power import register as register_power
from ptero_mcp.tools.schedules import register as register_schedules
from ptero_mcp.tools.servers import register as register_servers


def register_all(mcp: FastMCP, ctx: ToolContext) -> None:
    register_servers(mcp, ctx)
    register_power(mcp, ctx)
    register_console(mcp, ctx)
    register_activity(mcp, ctx)
    register_backups(mcp, ctx)
    register_databases(mcp, ctx)
    register_schedules(mcp, ctx)
