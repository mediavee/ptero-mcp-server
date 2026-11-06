"""Tool-boundary behaviour: what a client of the MCP server actually receives."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from ptero_mcp.config import Settings
from ptero_mcp.server import build_asgi_app
from tests.conftest import Rig

pytestmark = pytest.mark.anyio


async def call(rig: Rig, tool: str, **args: Any) -> dict[str, Any]:
    async with Client(rig.mcp) as client:
        result = await client.call_tool(tool, args)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


async def call_text(rig: Rig, tool: str, **args: Any) -> str:
    async with Client(rig.mcp) as client:
        result = await client.call_tool(tool, args)
    return str(getattr(result.content[0], "text", ""))


# ── 1. matched only means something when expect was given ──────────────────


async def test_run_command_without_expect_has_no_matched(rig: Rig) -> None:
    rig.client.reply = lambda: rig.emit("There are 0 players online")
    out = await call(rig, "run_command", server_id="abc", command="list", wait_ms=50)
    assert "matched" not in out
    assert "warning" not in out
    assert [ln["line"] for ln in out["lines"]] == ["There are 0 players online"]


async def test_run_command_expect_seen_is_matched_without_warning(rig: Rig) -> None:
    rig.client.reply = lambda: rig.emit("There are 0 players online")
    out = await call(
        rig, "run_command", server_id="abc", command="list", wait_ms=500, expect="players online"
    )
    assert out["matched"] is True
    assert "warning" not in out


async def test_run_command_expect_not_seen_says_so_with_numbers(rig: Rig) -> None:
    rig.client.reply = lambda: rig.emit("Unknown command")
    out = await call(
        rig, "run_command", server_id="abc", command="list", wait_ms=60, expect="players online"
    )
    assert out["matched"] is False
    m = re.fullmatch(r"expect not seen within (\d+) ms; 1 lines captured", out["warning"])
    assert m is not None, out["warning"]
    assert int(m.group(1)) >= 60


async def test_wait_console_without_expect_has_no_matched(rig: Rig) -> None:
    rig.emit_later("Done (3.1s)!")
    out = await call(rig, "wait_console", server_id="abc", wait_ms=60)
    assert "matched" not in out
    assert "warning" not in out


async def test_wait_console_expect_not_seen_says_so_with_numbers(rig: Rig) -> None:
    out = await call(rig, "wait_console", server_id="abc", wait_ms=60, expect="Done")
    assert out["matched"] is False
    assert re.fullmatch(r"expect not seen within \d+ ms; 0 lines captured", out["warning"])


async def test_wait_console_expect_seen_in_history_is_matched(rig: Rig) -> None:
    rig.emit("Done (3.1s)!")
    out = await call(rig, "wait_console", server_id="abc", wait_ms=60, expect="Done", since_ms=0)
    assert out["matched"] is True
    assert "warning" not in out


# ── 2. every result names the panel ────────────────────────────────────────


async def test_label_from_environment_is_on_every_kind_of_result(labelled_rig: Rig) -> None:
    rig = labelled_rig
    assert (await call(rig, "send_command", server_id="abc", command="list"))["panel"] == "dev"
    assert (await call(rig, "run_command", server_id="abc", command="list", wait_ms=0))[
        "panel"
    ] == "dev"
    assert (await call(rig, "tail_console", server_id="abc"))["panel"] == "dev"
    assert (await call(rig, "get_server", server_id="abc"))["panel"] == "dev"


async def test_unset_label_falls_back_to_panel_host(rig: Rig) -> None:
    out = await call(rig, "send_command", server_id="abc", command="list")
    assert out["panel"] == "panel.dev.example.com"


async def test_panel_is_in_the_text_content_clients_read(labelled_rig: Rig) -> None:
    text = await call_text(labelled_rig, "send_command", server_id="abc", command="list")
    assert json.loads(text)["panel"] == "dev"


async def test_non_object_result_is_wrapped_not_dropped(labelled_rig: Rig) -> None:
    labelled_rig.client.server = None  # type: ignore[assignment]
    out = await call(labelled_rig, "get_server", server_id="abc")
    assert out == {"panel": "dev", "result": None}


async def test_errors_name_the_panel_too(labelled_rig: Rig) -> None:
    async with Client(labelled_rig.mcp) as client:
        with pytest.raises(ToolError, match=r"^\[panel dev\] .*regex"):
            await client.call_tool("tail_console", {"server_id": "abc", "match": "("})


# ── 3. send_command is a receipt, not an effect ────────────────────────────


async def test_send_command_states_it_is_a_send_receipt_and_names_the_readers(rig: Rig) -> None:
    out = await call(rig, "send_command", server_id="abc", command="list")
    assert out["ok"] is True
    note = out["note"]
    assert "send receipt" in note.lower()
    for reader in ("tail_console", "wait_console", "run_command"):
        assert reader in note


# ── review regressions ─────────────────────────────────────────────────────


async def test_plain_text_success_stays_a_success(labelled_rig: Rig) -> None:
    labelled_rig.client.server = "restarting soon"  # type: ignore[assignment]
    out = await call(labelled_rig, "get_server", server_id="abc")
    assert out == {"panel": "dev", "result": "restarting soon"}


async def test_upstream_panel_key_cannot_override_the_label(labelled_rig: Rig) -> None:
    labelled_rig.client.server = {"panel": "spoofed.example", "object": "server"}
    out = await call(labelled_rig, "get_server", server_id="abc")
    assert out["panel"] == "dev"
    assert out["object"] == "server"


@pytest.fixture
async def http_mcp() -> AsyncIterator[Callable[[str], Client[Any]]]:
    """The real ASGI app, so the panel comes from request headers like in production."""
    settings = Settings.model_validate({"mcp_auth_token": "tok"})
    app = build_asgi_app(settings)

    def connect(panel_url: str) -> Client[Any]:
        def factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
            follow_redirects: bool = True,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers=headers,
                timeout=timeout or httpx.Timeout(30.0),
                auth=auth,
                follow_redirects=follow_redirects,
            )

        return Client(
            StreamableHttpTransport(
                "http://test/mcp",
                headers={
                    "Authorization": "Bearer tok",
                    "X-Pterodactyl-Url": panel_url,
                    "X-Pterodactyl-Key": "k",
                },
                httpx_client_factory=factory,
            )
        )

    async with app.router.lifespan_context(app):
        yield connect


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_server", {}),  # argument validation
        ("get_server", {"server_id": 5}),  # argument type
        ("no_such_tool", {}),  # unknown tool
    ],
)
async def test_every_error_names_the_panel_of_the_request_headers(
    http_mcp: Callable[[str], Client[Any]], tool: str, args: dict[str, Any]
) -> None:
    async with http_mcp("https://prod.example/") as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    assert result.is_error
    assert str(getattr(result.content[0], "text", "")).startswith("[panel prod.example] ")
