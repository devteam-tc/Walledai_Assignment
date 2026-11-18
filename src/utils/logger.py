"""
Structured logging setup for the MCP Monitor application.

Uses Python's stdlib logging with a JSON-friendly formatter so logs
can be parsed by monitoring tools, and a human-readable console handler.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line to stdout/file."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any extra dict fields attached by the caller
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "id", "levelname", "levelno",
                "lineno", "message", "module", "msecs", "msg", "name",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName",
            }:
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class _ConsoleFormatter(logging.Formatter):
    """Human-readable coloured console formatter."""

    _COLOURS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        colour = self._COLOURS.get(record.levelname, "")
        reset = self._RESET
        ts = datetime.now().strftime("%H:%M:%S")
        return f"{colour}[{ts}] [{record.levelname:<8}] {record.name}: {record.getMessage()}{reset}"


_configured = False


def configure_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """Call once at application startup to wire up handlers."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler (stderr so it doesn't pollute stdio proxy stdout)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(_ConsoleFormatter())
    root.addHandler(ch)

    # Optional file handler (JSON)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(_JsonFormatter())
        root.addHandler(fh)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "mitmproxy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Factory used by every module: ``logger = get_logger(__name__)``."""
    return logging.getLogger(name)
