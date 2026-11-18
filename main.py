"""
main.py — Application entry point.

Launches three components concurrently:

  1. Authorization Server  (FastAPI/uvicorn in a background thread)
  2. GUI Popup             (tkinter, must run on the main thread)
  3. Optional stdio proxy  (spawned on demand — usually run separately
                            as the MCP server command in Claude Desktop config)

Usage:
    python main.py                    # Launch auth server + GUI
    python main.py --mode server      # Auth server only (headless)
    python main.py --mode proxy \
        --server-name my-server \
        -- python /path/to/mcp_server.py   # Proxy only (called by Claude Desktop)
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from src.utils.config import settings
from src.utils.logger import configure_logging, get_logger

configure_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _handle_signal(signum, frame):  # type: ignore[no-untyped-def]
    logger.info("Signal %d received — shutting down", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_signal)
if hasattr(signal, "SIGBREAK"):   # Windows Ctrl+Break
    signal.signal(signal.SIGBREAK, _handle_signal)


# ---------------------------------------------------------------------------
# Component launchers
# ---------------------------------------------------------------------------

def _start_auth_server() -> threading.Thread:
    """Start the FastAPI auth server in a daemon thread."""
    from src.core.authorization_server import run_server

    t = threading.Thread(target=run_server, name="auth-server", daemon=True)
    t.start()
    logger.info("Authorization server thread started (port %d)", settings.auth_server_port)
    return t


def _wait_for_server(timeout: float = 10.0) -> bool:
    """Block until the auth server is reachable or timeout expires."""
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{settings.auth_server_url}/health", timeout=1.0)
            if r.status_code == 200:
                logger.info("Authorization server is ready")
                return True
        except Exception:
            pass
        time.sleep(0.3)
    logger.warning("Auth server did not become ready within %.1fs", timeout)
    return False


def _run_gui() -> None:
    """Run the Tkinter popup UI (must be called from the main thread)."""
    from src.ui.popup_ui import run_popup
    run_popup()


def _run_proxy(server_cmd: list, server_name: str) -> None:
    """Run the stdio proxy (usually called from Claude Desktop subprocess)."""
    from src.proxy.stdio_proxy import main as proxy_main
    proxy_main(server_command=server_cmd, server_name=server_name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP Communication Monitoring & User Authorization System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["full", "server", "gui", "proxy"],
        default="full",
        help=(
            "full   = auth server + GUI (default)\n"
            "server = auth server only (headless)\n"
            "gui    = GUI only (expects server already running)\n"
            "proxy  = stdio proxy only (used by Claude Desktop config)"
        ),
    )
    parser.add_argument(
        "--server-name",
        default="mcp-server",
        help="Label for the MCP server (used in proxy mode)",
    )
    parser.add_argument(
        "server_cmd",
        nargs=argparse.REMAINDER,
        help="[proxy mode] Command + args to start the real MCP server (after --)",
    )

    args = parser.parse_args()

    # Strip leading '--' separator if present
    server_cmd = [a for a in args.server_cmd if a != "--"]

    if args.mode == "proxy":
        if not server_cmd:
            parser.error("proxy mode requires the real MCP server command after --")
        _run_proxy(server_cmd, args.server_name)
        return

    if args.mode in ("full", "server"):
        _start_auth_server()
        ready = _wait_for_server()
        if not ready and args.mode == "server":
            logger.warning("Server may not be fully ready yet")

    if args.mode == "server":
        logger.info("Running in headless server mode — press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down")
        return

    if args.mode in ("full", "gui"):
        logger.info("Starting GUI popup (main thread)")
        _run_gui()


if __name__ == "__main__":
    main()
