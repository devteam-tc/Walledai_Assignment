"""
Central configuration management.

Values are read from environment variables (with sane defaults) and from an
optional ``.env`` file in the project root.  Pydantic Settings validates
and coerces all values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    from pydantic import BaseSettings  # type: ignore[no-redef]
    SettingsConfigDict = None  # type: ignore[assignment,misc]

from pydantic import Field


class Settings(BaseSettings):
    """All tuneable application settings."""

    # --- Authorization server ---
    auth_server_host: str = Field("127.0.0.1", validation_alias="MCP_AUTH_HOST")
    auth_server_port: int = Field(8765, validation_alias="MCP_AUTH_PORT")

    # --- Request timeouts ---
    decision_timeout_seconds: float = Field(
        120.0,
        validation_alias="MCP_DECISION_TIMEOUT",
        description="Seconds to wait for user decision before auto-timeout",
    )

    # --- Proxy ---
    proxy_log_file: Optional[str] = Field(None, validation_alias="MCP_PROXY_LOG")

    # --- HTTP proxy (mitmproxy) ---
    mitmproxy_port: int = Field(8080, validation_alias="MCP_MITMPROXY_PORT")
    mitmproxy_host: str = Field("127.0.0.1", validation_alias="MCP_MITMPROXY_HOST")

    # --- UI ---
    popup_always_on_top: bool = Field(True, validation_alias="MCP_POPUP_ON_TOP")
    popup_poll_interval_ms: int = Field(500, validation_alias="MCP_POPUP_POLL_MS")

    # --- Logging ---
    log_level: str = Field("INFO", validation_alias="MCP_LOG_LEVEL")
    log_file: Optional[str] = Field(
        default=str(Path.home() / ".mcp_monitor" / "mcp_monitor.log"),
        validation_alias="MCP_LOG_FILE",
    )

    # --- Cleanup ---
    store_cleanup_interval_seconds: float = Field(
        60.0, validation_alias="MCP_CLEANUP_INTERVAL"
    )

    if SettingsConfigDict is not None:
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )
    else:
        class Config:  # type: ignore[no-redef]
            env_file = ".env"
            env_file_encoding = "utf-8"
            case_sensitive = False

    @property
    def auth_server_url(self) -> str:
        return f"http://{self.auth_server_host}:{self.auth_server_port}"


# Singleton instance used throughout the application
settings = Settings()

# Ensure log directory exists
if settings.log_file:
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
