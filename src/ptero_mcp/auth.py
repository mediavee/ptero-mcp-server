"""Bearer-token authentication middleware for Starlette routes."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def bearer_auth(expected_token: str) -> Callable[
    [Request, Callable[[Request], Awaitable[Response]]],
    Awaitable[Response],
]:
    """Build a Starlette middleware enforcing a static bearer token.

    Tokens are compared with :func:`hmac.compare_digest` to dodge timing leaks.
    Returns a callable suitable for ``Starlette(middleware=[Middleware(...)])``
    or for use as a per-route guard via :func:`require_bearer`.
    """

    expected = expected_token.encode("utf-8")

    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _check(request, expected):
            return _unauthorized()
        return await call_next(request)

    return guard


def require_bearer(
    expected_token: str,
) -> Callable[[Request], Response | None]:
    """Return a synchronous guard usable inside route handlers.

    Returns ``None`` if the request is authorized, or a 401 :class:`Response`
    that the handler must propagate immediately.
    """

    expected = expected_token.encode("utf-8")

    def guard(request: Request) -> Response | None:
        if not _check(request, expected):
            return _unauthorized()
        return None

    return guard


def _check(request: Request, expected: bytes) -> bool:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return False
    provided = header[7:].strip().encode("utf-8")
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided, expected)


def _unauthorized() -> JSONResponse:
    # RFC 6750: declare the expected auth scheme explicitly so MCP clients
    # (notably Claude Code) don't fall back to OAuth 2.1 discovery on a bare
    # 401 and expose a virtual ``authenticate`` tool to the LLM.
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Unauthorized"},
            "id": None,
        },
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="ptero-mcp"'},
    )
