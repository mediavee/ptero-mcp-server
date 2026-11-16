"""Boundaries for panel origin, identifiers, retries, hints, replay, and startup."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr, ValidationError

from ptero_mcp.client import (
    PterodactylClient,
    PterodactylError,
    WebsocketCredentials,
    use_credentials,
)
from ptero_mcp.config import Settings
from ptero_mcp.console_hub import ConsoleHub, ConsoleLine, TailOptions, WaitOptions, _Session
from ptero_mcp.logging import configure_logging
from ptero_mcp.server import build_asgi_app
from tests.conftest import Rig

pytestmark = pytest.mark.anyio

SERVER = "abcdefgh"
BACKUP = "11111111-1111-1111-1111-111111111111"
WINGS = f"wss://node.example:8080/api/servers/{BACKUP}/ws"
TOKEN = "tok"


def _settings() -> Settings:
    return Settings.model_validate({"mcp_auth_token": TOKEN})


class _Transport:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.seen: list[httpx.URL] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.seen.append(request.url)
            return handler(request)

        self._handler = record

    async def install(self, client: PterodactylClient) -> None:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(self._handler))


def _client() -> PterodactylClient:
    return PterodactylClient(_settings())


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": {"token": "t", "socket": WINGS}})


# ── panel origin ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "panel_url",
    [
        "https://panel.example/latest/meta-data#",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/#",
        "https://panel.example/api/client?x=1",
        "https://user:pw@panel.example/",
    ],
)
async def test_panel_url_with_path_query_fragment_or_userinfo_is_not_requested(
    panel_url: str,
) -> None:
    client = _client()
    transport = _Transport(_ok)
    await transport.install(client)
    try:
        with use_credentials(panel_url, "key"), pytest.raises(ValueError):
            await client.get_server(SERVER)
        assert transport.seen == []
    finally:
        await client.aclose()


async def test_origin_is_what_the_client_calls() -> None:
    client = _client()
    transport = _Transport(_ok)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example:8443/", "key"):
            await client.get_server(SERVER)
        assert [str(url) for url in transport.seen] == [
            f"https://panel.example:8443/api/client/servers/{SERVER}"
        ]
    finally:
        await client.aclose()


async def test_private_origin_without_a_path_is_allowed() -> None:
    """A panel may be on a private address on purpose; only the path is refused."""
    client = _client()
    transport = _Transport(_ok)
    await transport.install(client)
    try:
        with use_credentials("http://[::1]:8000/", "key"):
            await client.get_server(SERVER)
        assert transport.seen[0].host == "::1"
        assert transport.seen[0].path == f"/api/client/servers/{SERVER}"
    finally:
        await client.aclose()


async def test_middleware_rejects_a_path_and_does_not_echo_it() -> None:
    app = build_asgi_app(_settings())
    raw = "http://169.254.169.254/latest/meta-data#"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        resp = await http.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Pterodactyl-Url": raw,
                "X-Pterodactyl-Key": "key",
            },
        )
    assert resp.status_code == 400
    assert "origin" in resp.json()["error"]["message"]
    assert "meta-data" not in resp.text
    assert "169.254.169.254" not in resp.text


# ── identifiers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "invoke",
    [
        lambda c: c.get_server(f"{SERVER}/files/list"),
        lambda c: c.get_backup(SERVER, "../../abcdefgh/files/list"),
        lambda c: c.rotate_database_password(SERVER, "abcd/../x"),
    ],
)
async def test_identifier_cannot_add_a_path_segment(
    invoke: Callable[[PterodactylClient], Awaitable[Any]],
) -> None:
    client = _client()
    transport = _Transport(_ok)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"), pytest.raises(ValueError):
            await invoke(client)
        assert transport.seen == []
    finally:
        await client.aclose()


async def test_valid_ids_keep_their_routes() -> None:
    client = _client()
    transport = _Transport(_ok)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"):
            await client.get_backup(SERVER, BACKUP)
            await client.rotate_database_password(SERVER, "Ab12Cd34")
        assert transport.seen[0].path == f"/api/client/servers/{SERVER}/backups/{BACKUP}"
        assert (
            transport.seen[1].path
            == f"/api/client/servers/{SERVER}/databases/Ab12Cd34/rotate-password"
        )
    finally:
        await client.aclose()


async def test_tool_schema_rejects_a_server_id_path(rig: Rig) -> None:
    called = False

    async def get_server(server_id: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    rig.client.get_server = get_server  # type: ignore[method-assign]
    async with Client(rig.mcp) as mcp:
        with pytest.raises(ToolError):
            await mcp.call_tool("get_server", {"server_id": f"{SERVER}/files/list"})
    assert called is False


# ── retries ─────────────────────────────────────────────────────────────────


async def test_get_is_retried_after_a_server_error() -> None:
    client = _client()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(500, text="nope")
        return httpx.Response(200, json={"ok": True})

    transport = _Transport(handler)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"):
            result = await client.get_server(SERVER)
        assert result == {"ok": True}
        assert calls == ["GET", "GET"]
    finally:
        await client.aclose()


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_write_is_sent_once_when_the_panel_returns_500(method: str) -> None:
    client = _client()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(500, text="nope")

    transport = _Transport(handler)
    await transport.install(client)
    try:
        with (
            use_credentials("https://panel.example", "key"),
            pytest.raises(PterodactylError, match="500"),
        ):
            await client._request(method, f"/servers/{SERVER}/power", body={"signal": "kill"})
        assert calls == [method]
    finally:
        await client.aclose()


async def test_post_timeout_is_sent_once() -> None:
    client = _client()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.ReadTimeout("timed out", request=request)

    transport = _Transport(handler)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"), pytest.raises(PterodactylError):
            await client.send_power(SERVER, "restart")
        assert calls == ["POST"]
    finally:
        await client.aclose()


# ── annotations ─────────────────────────────────────────────────────────────


async def _annotations(rig: Rig, name: str) -> Any:
    tool = await rig.mcp.get_tool(name)
    assert tool is not None
    assert tool.annotations is not None
    return tool.annotations


async def test_power_and_restore_are_not_idempotent(rig: Rig) -> None:
    for name in ("power_action", "restore_backup"):
        ann = await _annotations(rig, name)
        assert ann.idempotentHint is False
        assert ann.destructiveHint is True


async def test_schedule_task_create_and_execute_are_destructive(rig: Rig) -> None:
    for name in ("create_schedule_task", "execute_schedule"):
        ann = await _annotations(rig, name)
        assert ann.destructiveHint is True
        assert ann.idempotentHint is False


async def test_delete_backup_stays_an_idempotent_destructive_tool(rig: Rig) -> None:
    ann = await _annotations(rig, "delete_backup")
    assert ann.destructiveHint is True
    assert ann.idempotentHint is True


async def test_creating_a_schedule_is_not_marked_destructive(rig: Rig) -> None:
    ann = await _annotations(rig, "create_schedule")
    assert ann.destructiveHint is not True


# ── console replay and disconnect ───────────────────────────────────────────


class _Creds:
    def __init__(self, socket: str) -> None:
        self.socket = socket

    async def get_websocket_credentials(self, server_id: str) -> WebsocketCredentials:
        return WebsocketCredentials("token", self.socket)


class _Socket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def push(self, raw: str) -> None:
        self._queue.put_nowait(raw)

    async def send(self, data: str) -> None:
        event = str(json.loads(data)["event"])
        self.sent.append(event)
        if event == "send logs":
            self.push(json.dumps({"event": "console output", "args": ["Done (3.2s)!"]}))

    async def recv(self) -> str:
        return await self._queue.get()

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        return await self.recv()


class _Connect:
    def __init__(self, socket: _Socket) -> None:
        self.socket = socket
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> _Connect:
        self.urls.append(url)
        return self

    async def __aenter__(self) -> _Socket:
        return self.socket

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("AUTH_BURST_S", 0.02),
        ("REPLAY_FIRST_S", 0.2),
        ("REPLAY_IDLE_S", 0.02),
    ):
        if hasattr(ConsoleHub, name):
            monkeypatch.setattr(ConsoleHub, name, value)


async def test_replayed_backlog_misses_since_ms_and_disconnect_clears_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast(monkeypatch)
    socket = _Socket()
    socket.push(json.dumps({"event": "auth success"}))
    socket.push(json.dumps({"event": "status", "args": ["running"]}))
    connect = _Connect(socket)
    monkeypatch.setattr("ptero_mcp.console_hub.websockets.connect", connect)

    hub = ConsoleHub(_settings(), cast(PterodactylClient, _Creds(WINGS)))
    session = _Session(
        server_id=SERVER,
        panel_url="https://panel.example",
        api_key="key",
        buffer=deque(maxlen=100),
    )
    hub._sessions[("https://panel.example", "key", SERVER)] = session
    task = asyncio.create_task(hub._connect_and_pump(session))
    try:
        for _ in range(200):
            if any(ln.line == "Done (3.2s)!" for ln in session.buffer):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("backlog line was not ingested")

        with use_credentials("https://panel.example", "key"):
            hidden = await hub.tail(SERVER, TailOptions(since_ms=0, ready_timeout_ms=1000))
            shown = await hub.tail(SERVER, TailOptions(ready_timeout_ms=1000))
            waited = await hub.wait_for_output(
                SERVER, WaitOptions(wait_ms=40, expect="Done", since_ms=0, ready_timeout_ms=1000)
            )
        assert all(ln.line != "Done (3.2s)!" for ln in hidden.lines)
        assert any(ln.line == "Done (3.2s)!" for ln in shown.lines)
        assert waited.matched is False
        assert connect.urls == [WINGS]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not session.ready.is_set()


async def test_socket_on_another_host_is_the_wings_url() -> None:
    """The node FQDN is not the panel host; a matching Wings path is accepted."""
    # Imported lazily so a missing helper fails this test rather than collection.
    from ptero_mcp.urls import wings_socket_url

    assert wings_socket_url(WINGS) == WINGS
    with pytest.raises(ValueError):
        wings_socket_url("ws://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        wings_socket_url(f"wss://user:pw@node.example:8080/api/servers/{BACKUP}/ws")
    with pytest.raises(ValueError):
        wings_socket_url(f"{WINGS}?token=1")


# ── startup token and logs ──────────────────────────────────────────────────


def test_empty_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(_env_file=None, mcp_auth_token="")
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(_env_file=None, mcp_auth_token="   ")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "")
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(_env_file=None)


def test_non_empty_token_is_accepted() -> None:
    settings = Settings.model_validate({"mcp_auth_token": "t"})
    assert isinstance(settings.mcp_auth_token, SecretStr)
    assert settings.mcp_auth_token.get_secret_value() == "t"


async def test_error_log_omits_body_and_panel_url(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    client = _client()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom https://panel.example/secret-path?x=1", request=request)

    transport = _Transport(handler)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"), pytest.raises(PterodactylError):
            await client._request("POST", f"/servers/{SERVER}/power", retries=1)
    finally:
        await client.aclose()
    captured = capsys.readouterr().out
    assert "panel_request_network_error" in captured
    assert "secret-path" not in captured
    assert "https://panel.example" not in captured

    client = _client()

    def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="upstream-secret-body")

    transport = _Transport(failed)
    await transport.install(client)
    try:
        with use_credentials("https://panel.example", "key"), pytest.raises(PterodactylError):
            await client.get_server(SERVER)
    finally:
        await client.aclose()
    captured = capsys.readouterr().out
    assert "panel_request_failed" in captured
    assert "upstream-secret-body" not in captured


def test_replay_flag_defaults_off_for_ordinary_lines() -> None:
    line = ConsoleLine(ts=0, line="ok")
    assert line.replay is False
