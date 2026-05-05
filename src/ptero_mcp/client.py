"""Async wrapper around the Pterodactyl Client API.

Only methods used by the MCP tools are implemented. Responses are returned as
parsed JSON; the panel uses a JSON:API-flavored ``object``/``attributes``
fractal envelope — callers are responsible for unwrapping when they care.

Auth is configured at startup via ``PTERODACTYL_URL`` / ``PTERODACTYL_KEY``
env vars and baked into the httpx client.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx

from ptero_mcp.config import Settings
from ptero_mcp.logging import get_logger

log = get_logger(__name__)


PowerSignal = Literal["start", "stop", "restart", "kill"]
ScheduleAction = Literal["command", "power", "backup"]
ActivitySort = Literal["timestamp", "-timestamp"]


class PterodactylError(Exception):
    """Wraps a non-2xx response from the panel."""

    def __init__(self, message: str, status: int, body: object) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class WebsocketCredentials:
    __slots__ = ("socket", "token")

    def __init__(self, token: str, socket: str) -> None:
        self.token = token
        self.socket = socket


class PterodactylClient:
    """Thin async wrapper over the Pterodactyl Client API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.panel_url = settings.panel_url
        self._http = httpx.AsyncClient(
            base_url=f"{self.panel_url}/api/client",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.pterodactyl_key.get_secret_value()}",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=httpx.AsyncHTTPTransport(retries=2),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ──────────────────────────── core request ────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        retries: int = 3,
    ) -> Any:
        params = {k: v for k, v in (query or {}).items() if v is not None}

        last_exc: Exception | None = None
        backoff = 0.4
        for attempt in range(retries):
            try:
                resp = await self._http.request(
                    method, path, params=params, json=body
                )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt == retries - 1:
                    log.error(
                        "panel_request_network_error",
                        method=method,
                        path=path,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    raise PterodactylError(
                        f"Pterodactyl API {method} {path} network error: {exc}", 0, None
                    ) from exc
                log.warning(
                    "panel_request_network_retry",
                    method=method,
                    path=path,
                    attempt=attempt + 1,
                    backoff_s=backoff,
                    error=str(exc),
                )
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code >= 500 and attempt < retries - 1:
                log.warning(
                    "panel_request_server_error_retry",
                    method=method,
                    path=path,
                    status=resp.status_code,
                    attempt=attempt + 1,
                    backoff_s=backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 204:
                log.debug("panel_request", method=method, path=path, status=204)
                return None

            parsed: Any
            text = resp.text
            if text:
                try:
                    parsed = resp.json()
                except ValueError:
                    parsed = text
            else:
                parsed = None

            if not resp.is_success:
                log.warning(
                    "panel_request_failed",
                    method=method,
                    path=path,
                    status=resp.status_code,
                    body=parsed,
                )
                raise PterodactylError(
                    f"Pterodactyl API {method} {path} failed: "
                    f"{resp.status_code} {resp.reason_phrase}",
                    resp.status_code,
                    parsed,
                )

            log.debug("panel_request", method=method, path=path, status=resp.status_code)
            return parsed

        # Unreachable: loop either returns or raises.
        raise PterodactylError(
            f"Pterodactyl API {method} {path} exhausted retries", 0, None
        ) from last_exc

    # ─────────────────────────────── servers ───────────────────────────────

    async def list_servers(self, page: int | None = None, per_page: int | None = None) -> Any:
        return await self._request("GET", "", query={"page": page, "per_page": per_page})

    async def get_server(self, server_id: str) -> Any:
        return await self._request("GET", f"/servers/{server_id}")

    async def get_resources(self, server_id: str) -> Any:
        return await self._request("GET", f"/servers/{server_id}/resources")

    # ───────────────────────────────── power ───────────────────────────────

    async def send_power(self, server_id: str, signal: PowerSignal) -> None:
        await self._request("POST", f"/servers/{server_id}/power", body={"signal": signal})

    # ──────────────────────────────── console ──────────────────────────────

    async def send_command(self, server_id: str, command: str) -> None:
        await self._request("POST", f"/servers/{server_id}/command", body={"command": command})

    async def get_websocket_credentials(self, server_id: str) -> WebsocketCredentials:
        data = await self._request("GET", f"/servers/{server_id}/websocket")
        payload = data["data"]
        return WebsocketCredentials(token=payload["token"], socket=payload["socket"])

    # ──────────────────────────────── activity ─────────────────────────────

    async def get_activity_log(
        self,
        server_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
        event_filter: str | None = None,
        sort: ActivitySort | None = None,
    ) -> Any:
        return await self._request(
            "GET",
            f"/servers/{server_id}/activity",
            query={
                "page": page,
                "per_page": per_page,
                "filter[event]": event_filter,
                "sort": sort,
                # Without this the panel omits the actor relationship and
                # entries only carry an anonymous event type — useless for
                # "who did X".
                "include": "actor",
            },
        )

    # ──────────────────────────────── backups ──────────────────────────────

    async def list_backups(
        self, server_id: str, page: int | None = None, per_page: int | None = None
    ) -> Any:
        return await self._request(
            "GET",
            f"/servers/{server_id}/backups",
            query={"page": page, "per_page": per_page},
        )

    async def get_backup(self, server_id: str, backup_uuid: str) -> Any:
        return await self._request("GET", f"/servers/{server_id}/backups/{backup_uuid}")

    async def create_backup(
        self,
        server_id: str,
        *,
        name: str | None = None,
        ignored: str | None = None,
        is_locked: bool | None = None,
    ) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/backups",
            body={"name": name, "ignored": ignored, "is_locked": is_locked},
        )

    async def delete_backup(self, server_id: str, backup_uuid: str) -> None:
        await self._request("DELETE", f"/servers/{server_id}/backups/{backup_uuid}")

    async def toggle_backup_lock(self, server_id: str, backup_uuid: str) -> Any:
        return await self._request(
            "POST", f"/servers/{server_id}/backups/{backup_uuid}/lock"
        )

    async def restore_backup(
        self, server_id: str, backup_uuid: str, truncate: bool = False
    ) -> None:
        await self._request(
            "POST",
            f"/servers/{server_id}/backups/{backup_uuid}/restore",
            body={"truncate": truncate},
        )

    async def get_backup_download_url(self, server_id: str, backup_uuid: str) -> Any:
        return await self._request(
            "GET", f"/servers/{server_id}/backups/{backup_uuid}/download"
        )

    # ─────────────────────────────── databases ─────────────────────────────

    async def list_databases(self, server_id: str) -> Any:
        return await self._request(
            "GET", f"/servers/{server_id}/databases", query={"include": "password"}
        )

    async def create_database(self, server_id: str, database: str, remote: str) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/databases",
            body={"database": database, "remote": remote},
        )

    async def rotate_database_password(self, server_id: str, database_id: str) -> Any:
        return await self._request(
            "POST", f"/servers/{server_id}/databases/{database_id}/rotate-password"
        )

    async def delete_database(self, server_id: str, database_id: str) -> None:
        await self._request("DELETE", f"/servers/{server_id}/databases/{database_id}")

    # ─────────────────────────────── schedules ─────────────────────────────

    async def list_schedules(self, server_id: str) -> Any:
        return await self._request("GET", f"/servers/{server_id}/schedules")

    async def get_schedule(self, server_id: str, schedule_id: int) -> Any:
        return await self._request("GET", f"/servers/{server_id}/schedules/{schedule_id}")

    async def create_schedule(
        self,
        server_id: str,
        *,
        name: str,
        minute: str,
        hour: str,
        day_of_month: str,
        month: str,
        day_of_week: str,
        is_active: bool = True,
        only_when_online: bool = False,
    ) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/schedules",
            body={
                "name": name,
                "is_active": is_active,
                "only_when_online": only_when_online,
                "minute": minute,
                "hour": hour,
                "day_of_month": day_of_month,
                "month": month,
                "day_of_week": day_of_week,
            },
        )

    async def update_schedule(
        self,
        server_id: str,
        schedule_id: int,
        *,
        name: str,
        is_active: bool,
        minute: str,
        hour: str,
        day_of_month: str,
        month: str,
        day_of_week: str,
        only_when_online: bool = False,
    ) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/schedules/{schedule_id}",
            body={
                "name": name,
                "is_active": is_active,
                "only_when_online": only_when_online,
                "minute": minute,
                "hour": hour,
                "day_of_month": day_of_month,
                "month": month,
                "day_of_week": day_of_week,
            },
        )

    async def delete_schedule(self, server_id: str, schedule_id: int) -> None:
        await self._request("DELETE", f"/servers/{server_id}/schedules/{schedule_id}")

    async def execute_schedule(self, server_id: str, schedule_id: int) -> None:
        await self._request("POST", f"/servers/{server_id}/schedules/{schedule_id}/execute")

    async def create_schedule_task(
        self,
        server_id: str,
        schedule_id: int,
        *,
        action: ScheduleAction,
        time_offset: int,
        payload: str | None = None,
        sequence_id: int | None = None,
        continue_on_failure: bool = False,
    ) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/schedules/{schedule_id}/tasks",
            body={
                "action": action,
                "payload": payload or "",
                "time_offset": time_offset,
                "sequence_id": sequence_id,
                "continue_on_failure": continue_on_failure,
            },
        )

    async def update_schedule_task(
        self,
        server_id: str,
        schedule_id: int,
        task_id: int,
        *,
        action: ScheduleAction,
        time_offset: int,
        payload: str | None = None,
        sequence_id: int | None = None,
        continue_on_failure: bool = False,
    ) -> Any:
        return await self._request(
            "POST",
            f"/servers/{server_id}/schedules/{schedule_id}/tasks/{task_id}",
            body={
                "action": action,
                "payload": payload or "",
                "time_offset": time_offset,
                "sequence_id": sequence_id,
                "continue_on_failure": continue_on_failure,
            },
        )

    async def delete_schedule_task(
        self, server_id: str, schedule_id: int, task_id: int
    ) -> None:
        await self._request(
            "DELETE", f"/servers/{server_id}/schedules/{schedule_id}/tasks/{task_id}"
        )
