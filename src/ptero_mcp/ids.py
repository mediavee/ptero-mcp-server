"""Identifiers interpolated into Client API paths."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field

SERVER_ID_PATTERN = r"^[A-Za-z0-9]{8}$"
BACKUP_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# Hashids alphabet is alphanumeric; the length is set on the panel (default 8).
DATABASE_ID_PATTERN = r"^[A-Za-z0-9]{1,128}$"

_SERVER_ID = re.compile(SERVER_ID_PATTERN)
_BACKUP_UUID = re.compile(BACKUP_UUID_PATTERN)
_DATABASE_ID = re.compile(DATABASE_ID_PATTERN)

ServerId = Annotated[
    str,
    Field(pattern=SERVER_ID_PATTERN, description="Server identifier (8 alphanumeric characters)"),
]
BackupUuid = Annotated[str, Field(pattern=BACKUP_UUID_PATTERN, description="Backup UUID")]
DatabaseId = Annotated[
    str,
    Field(pattern=DATABASE_ID_PATTERN, description="Database identifier (hashid)"),
]


def require_server_id(value: str) -> str:
    if _SERVER_ID.fullmatch(value) is None:
        raise ValueError("Invalid server id")
    return value


def require_backup_uuid(value: str) -> str:
    if _BACKUP_UUID.fullmatch(value) is None:
        raise ValueError("Invalid backup uuid")
    return value


def require_database_id(value: str) -> str:
    if _DATABASE_ID.fullmatch(value) is None:
        raise ValueError("Invalid database id")
    return value


def require_numeric_id(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Invalid numeric id")
    return str(value)
