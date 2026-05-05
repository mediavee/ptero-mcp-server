"""Typed configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings.

    Values are loaded from process environment, then ``.env`` (if present).
    Field names use snake_case; env vars use SCREAMING_SNAKE_CASE.

    Panel URL and API key are **not** loaded here — they are provided
    per-request via the ``X-Pterodactyl-Url`` and ``X-Pterodactyl-Key``
    HTTP headers so a single instance can serve any number of panels and
    operators with strict isolation (one console buffer per
    panel + key + server triple).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    mcp_auth_token: SecretStr = Field(..., description="Bearer token for HTTP clients.")

    http_host: str = Field("0.0.0.0", description="HTTP bind host.")
    http_port: int = Field(3000, ge=1, le=65535, description="HTTP bind port.")

    console_buffer_size: int = Field(5000, ge=10, description="Per-server ring buffer size.")
    console_idle_ttl: int = Field(
        600, ge=10, description="Idle TTL in seconds before a console session is reaped."
    )

    log_level: str = Field("INFO", description="Logger level (DEBUG, INFO, WARNING, ERROR).")
    log_json: bool = Field(False, description="Emit logs as JSON.")

    @property
    def console_idle_ttl_ms(self) -> int:
        return self.console_idle_ttl * 1000


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
