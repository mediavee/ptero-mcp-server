"""Names the panel on every tool result, in one place."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt

from ptero_mcp.client import current_panel_url
from ptero_mcp.config import Settings


class PanelLabelMiddleware(Middleware):
    """Adds ``panel`` to results and prefixes errors, so prod and dev cannot be confused."""

    def __init__(self, settings: Settings) -> None:
        self._label = settings.panel_label

    def _panel(self) -> str:
        if self._label:
            return self._label
        return urlsplit(current_panel_url.get() or "").netloc.rpartition("@")[2] or "unknown"

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        panel = self._panel()
        try:
            result = await call_next(context)
        except Exception as exc:  # argument validation and unknown tools are not ToolErrors
            raise ToolError(f"[panel {panel}] {exc}") from exc

        body = result.structured_content
        payload = (
            {**body, "panel": panel}
            if isinstance(body, dict)
            else {"panel": panel, "result": _plain(result)}
        )
        return ToolResult(
            content=json.dumps(payload, separators=(",", ":")),
            structured_content=payload,
            meta=result.meta,
        )


def _plain(result: ToolResult) -> Any:
    """What a tool returned when it was not an object: its text block, parsed when it is JSON."""
    if not result.content:
        return None
    text = getattr(result.content[0], "text", None)
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
