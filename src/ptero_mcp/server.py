"""FastMCP application wiring (stdio transport, single tenant)."""

from __future__ import annotations

from fastmcp import FastMCP

from ptero_mcp import __version__
from ptero_mcp.client import PterodactylClient
from ptero_mcp.config import Settings, load_settings
from ptero_mcp.console_hub import ConsoleHub
from ptero_mcp.context import ToolContext
from ptero_mcp.logging import configure_logging, get_logger
from ptero_mcp.tools import register_all

log = get_logger(__name__)


def build_mcp(
    settings: Settings,
) -> tuple[FastMCP, ConsoleHub, PterodactylClient]:
    """Build the FastMCP server and register every tool."""
    client = PterodactylClient(settings)
    console_hub = ConsoleHub(settings, client)
    ctx = ToolContext(settings=settings, client=client, console_hub=console_hub)

    mcp = FastMCP(
        name="ptero-mcp",
        version=__version__,
        instructions=(
            "Operate Pterodactyl-managed servers: power, console (live tail/wait/run), "
            "backups, databases, schedules, activity log."
        ),
    )
    register_all(mcp, ctx)
    return mcp, console_hub, client


async def run_async() -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    mcp, console_hub, client = build_mcp(settings)
    await console_hub.start()
    log.info(
        "ptero_mcp_started",
        version=__version__,
        panel_url=settings.panel_url,
        buffer_size=settings.console_buffer_size,
        idle_ttl_s=settings.console_idle_ttl,
    )
    try:
        await mcp.run_async()
    finally:
        log.info("ptero_mcp_shutting_down")
        await console_hub.shutdown()
        await client.aclose()
