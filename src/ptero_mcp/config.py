"""Typed configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings.

    Values are loaded from process environment, then ``.env`` (if present).
    Field names use snake_case; env vars use SCREAMING_SNAKE_CASE.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    pterodactyl_url: HttpUrl = Field(
        ..., description="Base URL of the Pterodactyl panel (e.g. https://panel.example.com)."
    )
    pterodactyl_key: SecretStr = Field(..., description="Pterodactyl Client API key.")

    console_buffer_size: int = Field(5000, ge=10, description="Per-server ring buffer size.")
    console_idle_ttl: int = Field(
        600, ge=10, description="Idle TTL in seconds before a console session is reaped."
    )

    log_level: str = Field("INFO", description="Logger level (DEBUG, INFO, WARNING, ERROR).")
    log_json: bool = Field(False, description="Emit logs as JSON.")

    @property
    def console_idle_ttl_ms(self) -> int:
        return self.console_idle_ttl * 1000

    @property
    def panel_url(self) -> str:
        """Normalised panel URL (no trailing slash)."""
        return str(self.pterodactyl_url).rstrip("/")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
