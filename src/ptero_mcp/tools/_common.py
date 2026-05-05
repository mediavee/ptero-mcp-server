"""Shared helpers for tool handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastmcp.exceptions import ToolError

from ptero_mcp.client import PterodactylError
from ptero_mcp.logging import get_logger

log = get_logger(__name__)


def map_panel_errors[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Translate :class:`PterodactylError` into FastMCP-friendly errors.

    A 502 from the panel almost always means "server is not running" — surface
    that as an actionable :class:`ToolError` instead of a stack trace.
    """

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except PterodactylError as exc:
            log.warning(
                "panel_tool_error",
                tool=fn.__name__,
                status=exc.status,
                body=exc.body,
            )
            if exc.status == 502:
                raise ToolError(
                    "Cannot reach Wings: server is likely not running (502 from panel)."
                ) from exc
            raise ToolError(f"Pterodactyl API error ({exc.status}): {exc}") from exc

    return wrapper


def ok(**fields: Any) -> dict[str, Any]:
    """Tiny helper to build the standard ``{ok: true, ...}`` payload."""
    return {"ok": True, **fields}


# ──────────────────────────── tool annotation presets ──────────────────────────
#
# MCP tool annotations — see https://modelcontextprotocol.io/specification.
# Hints clients (Claude Code/Desktop) on safety so they can colour tools, prompt
# before destructive ops, and skip confirmations on read-only ones. All our
# tools talk to the panel API, hence ``openWorldHint=True`` everywhere.

READ_ONLY: dict[str, Any] = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

DESTRUCTIVE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

WRITE: dict[str, Any] = {
    "readOnlyHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
