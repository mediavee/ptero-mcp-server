"""Panel and Wings URL checks.

A panel URL is an origin. Anything past the host is rejected so the Client API
prefix cannot be swallowed by a path, query, or fragment. The Wings socket is a
different host: the panel builds it from the node FQDN and daemon port.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_URL_IN_TEXT = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_WINGS_PATH = re.compile(
    r"^/api/servers/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"/ws$"
)

_PANEL_URL = (
    "X-Pterodactyl-Url must be an http(s) origin without a path, query, fragment, or userinfo"
)
_WINGS_URL = "Wings socket must be a ws(s) node URL without userinfo, query, or fragment"


def normalise_panel_origin(value: str) -> str:
    """Return ``scheme://host[:port]`` or raise ``ValueError``.

    A trailing slash is the origin, not a path segment, and is dropped.
    """
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise ValueError(_PANEL_URL)
    return _origin(parsed.scheme, parsed.hostname, parsed.port)


def wings_socket_url(value: str) -> str:
    """Return the Wings websocket URL, or raise ``ValueError``.

    The host may differ from the panel: Pterodactyl's websocket endpoint
    publishes ``{node scheme}://{node fqdn}:{daemon port}/api/servers/{uuid}/ws``.
    """
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or _WINGS_PATH.fullmatch(parsed.path) is None
    ):
        raise ValueError(_WINGS_URL)
    return _origin(parsed.scheme, parsed.hostname, parsed.port) + parsed.path


def panel_host(value: str) -> str:
    return urlsplit(value).hostname or "unknown"


def redact_url(text: str) -> str:
    return _URL_IN_TEXT.sub("[redacted-url]", text)


def _origin(scheme: str, hostname: str, port: int | None) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is None:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"
