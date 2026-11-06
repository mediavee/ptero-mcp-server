"""Fixtures: a real ConsoleHub and tool layer over a faked panel client (no network)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
from fastmcp import FastMCP

from ptero_mcp.client import PterodactylClient, use_credentials
from ptero_mcp.config import Settings
from ptero_mcp.console_hub import ConsoleHub, ConsoleLine, _Session
from ptero_mcp.context import ToolContext
from ptero_mcp.tools import register_all

PANEL_URL = "https://panel.dev.example.com/"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakePanelClient:
    """Stands in for PterodactylClient; ``reply`` lines are pushed on send_command."""

    def __init__(self) -> None:
        self.reply: Callable[[], None] = lambda: None
        self.server: dict[str, Any] = {"object": "server", "attributes": {"identifier": "abc"}}

    async def send_command(self, server_id: str, command: str) -> None:
        self.reply()

    async def get_server(self, server_id: str) -> Any:
        return self.server


class _FixedSessionHub(ConsoleHub):
    """The real hub, with the WebSocket session replaced by an in-memory one."""

    session: _Session

    async def _ensure_session(self, server_id: str) -> _Session:
        return self.session


class Rig:
    def __init__(self, label: str | None) -> None:
        self.client = FakePanelClient()
        settings = Settings.model_validate({"mcp_auth_token": "t", "panel_label": label})
        panel = cast(PterodactylClient, self.client)
        self.hub = _FixedSessionHub(settings, panel)
        self.session = _Session(
            server_id="abc", panel_url=PANEL_URL, api_key="k", buffer=deque(maxlen=100)
        )
        self.session.ready.set()
        self.hub.session = self.session
        self.mcp = FastMCP("test")
        register_all(self.mcp, ToolContext(settings=settings, client=panel, console_hub=self.hub))

    def emit(self, line: str) -> None:
        entry = ConsoleLine(ts=0, line=line)
        self.session.buffer.append(entry)
        self.hub._fanout(self.session, entry)

    def emit_later(self, line: str, delay: float = 0.01) -> None:
        asyncio.get_running_loop().call_later(delay, self.emit, line)


@pytest.fixture
def credentials() -> Iterator[None]:
    with use_credentials(PANEL_URL, "k"):
        yield


@pytest.fixture
def rig(credentials: None) -> Rig:
    return Rig(label=None)


@pytest.fixture
def labelled_rig(credentials: None) -> Rig:
    return Rig(label="dev")
