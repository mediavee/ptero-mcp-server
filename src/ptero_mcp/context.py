"""Shared dependencies wired into every tool handler.

Plugins/forks can swap in their own fields here without touching tool
modules — keep this dataclass small and composable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ptero_mcp.client import PterodactylClient
from ptero_mcp.config import Settings
from ptero_mcp.console_hub import ConsoleHub


@dataclass(slots=True, frozen=True)
class ToolContext:
    settings: Settings
    client: PterodactylClient
    console_hub: ConsoleHub
