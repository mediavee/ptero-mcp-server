"""Shared helpers for tool handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from fastmcp.exceptions import ToolError

from ptero_mcp.client import PterodactylError

P = ParamSpec("P")
R = TypeVar("R")


def map_panel_errors(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Translate :class:`PterodactylError` into FastMCP-friendly errors.

    A 502 from the panel almost always means "server is not running" — surface
    that as an actionable :class:`ToolError` instead of a stack trace.
    """

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except PterodactylError as exc:
            if exc.status == 502:
                raise ToolError(
                    "Cannot reach Wings: server is likely not running (502 from panel)."
                ) from exc
            raise ToolError(f"Pterodactyl API error ({exc.status}): {exc}") from exc

    return wrapper


def ok(**fields: Any) -> dict[str, Any]:
    """Tiny helper to build the standard ``{ok: true, ...}`` payload."""
    return {"ok": True, **fields}
