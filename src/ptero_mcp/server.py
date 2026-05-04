"""FastMCP application wiring: lifespan, custom routes, auth, transport."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount

from ptero_mcp import __version__
from ptero_mcp.auth import bearer_auth
from ptero_mcp.client import PterodactylClient
from ptero_mcp.config import Settings, load_settings
from ptero_mcp.console_hub import ConsoleHub
from ptero_mcp.context import ToolContext
from ptero_mcp.logging import configure_logging, get_logger
from ptero_mcp.streams import make_sse_handler
from ptero_mcp.tools import register_all

log = get_logger(__name__)


def build_mcp(
    settings: Settings,
) -> tuple[FastMCP, ConsoleHub, PterodactylClient]:
    """Build the FastMCP server, register tools and the SSE / health routes.

    Returns the configured FastMCP plus the long-lived components driven by
    the ASGI lifespan (so they can be reused in tests or alternate transports).
    """

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

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    sse_handler = make_sse_handler(console_hub)

    @mcp.custom_route("/streams/{server_id}", methods=["GET"])
    async def stream(request: Request) -> Response:
        return await sse_handler(request)

    return mcp, console_hub, client


def build_asgi_app(settings: Settings) -> Starlette:
    """Build the full ASGI app: FastMCP + bearer auth + console hub lifespan."""

    mcp, console_hub, client = build_mcp(settings)

    # FastMCP 3.x returns a fully-formed Starlette app rooted at ``path``.
    # We wrap it in a parent Starlette so we can:
    # (1) layer our bearer auth across every route,
    # (2) chain the console hub's lifespan with FastMCP's own session-manager
    #     lifespan in the right order.
    mcp_app = mcp.http_app(path="/mcp")

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Start the FastMCP session manager first; it owns the in-flight
        # MCP transports and must outlive any request.
        async with mcp_app.router.lifespan_context(app):
            await console_hub.start()
            log.info(
                "ptero_mcp_started",
                panel=settings.panel_base,
                buffer_size=settings.console_buffer_size,
                idle_ttl_s=settings.console_idle_ttl,
            )
            try:
                yield
            finally:
                log.info("ptero_mcp_shutting_down")
                await console_hub.shutdown()
                await client.aclose()

    expected_token = settings.mcp_auth_token.get_secret_value()
    return Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[Middleware(_AuthMiddleware, expected_token=expected_token)],
        lifespan=lifespan,
    )


class _AuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token gate for everything except /healthz."""

    def __init__(self, app, expected_token: str) -> None:
        super().__init__(app)
        self._guard = bearer_auth(expected_token)

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        return await self._guard(request, call_next)


async def run() -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app = build_asgi_app(settings)

    config = uvicorn.Config(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,  # let structlog/stdlib bridge handle uvicorn logs
        access_log=False,
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    log.info(
        "ptero_mcp_listening",
        host=settings.http_host,
        port=settings.http_port,
        mcp_url=f"http://{settings.http_host}:{settings.http_port}/mcp",
        sse_url=f"http://{settings.http_host}:{settings.http_port}/streams/<server_id>",
    )
    try:
        await server.serve()
    except asyncio.CancelledError:
        pass
