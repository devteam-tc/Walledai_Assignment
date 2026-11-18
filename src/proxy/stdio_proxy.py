"""
stdio MCP Proxy for Claude Desktop.

How it works
------------
Claude Desktop normally launches your MCP server directly:

    Claude Desktop ──stdin/stdout──► MCP Server Process

This proxy inserts itself in the middle:

    Claude Desktop ──stdin/stdout──► mcp_proxy.py ──stdin/stdout──► Real MCP Server

To use it, replace the ``command``/``args`` in claude_desktop_config.json
with the path to this script (see install.py or README).

Interception logic
------------------
Every JSON-RPC message is inspected.  When the method is ``tools/call``,
the proxy submits the request to the authorization server and BLOCKS
until the user makes a decision:

  • ALLOWED → forward the message to the real MCP server as normal
  • BLOCKED → synthesise a JSON-RPC error response and return it to Claude
  • TIMEOUT → treat as BLOCKED (fail-safe)

All other message types (initialize, tools/list, notifications, …) are
forwarded transparently without user interaction.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

import httpx

from src.core.models import MCPRequest, MCPRequestSource, RequestStatus
from src.utils.config import settings
from src.utils.logger import configure_logging, get_logger

configure_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TOOLS_CALL_METHOD = "tools/call"
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_error_response(request_id: Any, code: int, message: str) -> bytes:
    """Return a newline-terminated JSON-RPC error frame."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def _extract_tool_info(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Pull tool name and params from a tools/call JSON-RPC payload."""
    params = payload.get("params", {})
    tool_name = params.get("name", "<unknown>")
    tool_params = params.get("arguments", params.get("input", {}))
    return tool_name, tool_params


def _submit_to_auth_server(request: MCPRequest) -> None:
    """POST the intercepted request to the running authorization server."""
    url = f"{settings.auth_server_url}/request"
    try:
        resp = httpx.post(url, content=request.model_dump_json(), timeout=5.0,
                         headers={"Content-Type": "application/json"})
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to submit to auth server: %s", exc)
        raise


def _wait_for_decision_http(request_id: str, timeout: float) -> RequestStatus:
    """
    Poll the auth server until a decision is recorded.
    Used when the proxy runs in a separate process from the server.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(
                f"{settings.auth_server_url}/request/{request_id}",
                timeout=3.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                st = data.get("status", "pending")
                if st != "pending":
                    return RequestStatus(st)
        except Exception:
            pass
        time.sleep(0.3)
    return RequestStatus.TIMEOUT


# ---------------------------------------------------------------------------
# Main proxy class
# ---------------------------------------------------------------------------

class StdioProxy:
    """
    Transparent stdin/stdout proxy with MCP tool-call authorization.

    Parameters
    ----------
    server_command:
        List of command + arguments that starts the real MCP server.
    mcp_server_name:
        Human-readable label for the server (shown in the popup).
    use_in_process_store:
        If True, communicate with the RequestStore directly (same process).
        If False, use HTTP calls to the external authorization server.
    """

    def __init__(
        self,
        server_command: List[str],
        mcp_server_name: str = "unknown-server",
        use_in_process_store: bool = False,
        store=None,  # Optional[RequestStore]
    ) -> None:
        self.server_command = server_command
        self.mcp_server_name = mcp_server_name
        self.use_in_process_store = use_in_process_store
        self.store = store  # only used when use_in_process_store=True

        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._running = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the real MCP server and start forwarding threads."""
        logger.info("Starting real MCP server: %s", " ".join(self.server_command))
        try:
            self._proc = subprocess.Popen(
                self.server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            logger.critical("Cannot launch MCP server: %s", exc)
            sys.exit(1)

        self._running.set()

        # Thread: Claude → Proxy (read from our stdin, process, write to server stdin)
        t_in = threading.Thread(
            target=self._forward_from_claude,
            name="claude-to-server",
            daemon=True,
        )
        # Thread: Server → Claude (read from server stdout, write to our stdout)
        t_out = threading.Thread(
            target=self._forward_from_server,
            name="server-to-claude",
            daemon=True,
        )
        # Thread: relay server stderr to our stderr
        t_err = threading.Thread(
            target=self._relay_stderr,
            name="server-stderr",
            daemon=True,
        )

        t_in.start()
        t_out.start()
        t_err.start()

        # Wait for the real server to exit (normal lifecycle)
        self._proc.wait()
        logger.info("Real MCP server exited with code %d", self._proc.returncode)

    # ------------------------------------------------------------------
    # Forwarding threads
    # ------------------------------------------------------------------

    def _forward_from_claude(self) -> None:
        """
        Read JSON-RPC messages from Claude Desktop (our stdin),
        intercept tool calls, then forward to the real server's stdin.
        """
        try:
            for raw_line in sys.stdin.buffer:
                if not raw_line.strip():
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON from Claude, forwarding raw: %r", raw_line[:120])
                    self._write_to_server(raw_line)
                    continue

                method = payload.get("method", "")
                rpc_id = payload.get("id")

                if method == TOOLS_CALL_METHOD:
                    allowed = self._handle_tool_call(payload, rpc_id)
                    if allowed:
                        self._write_to_server(raw_line)
                    else:
                        # Inject a blocked error back to Claude
                        err = _build_error_response(
                            rpc_id,
                            JSONRPC_INTERNAL_ERROR,
                            "MCP request blocked by user authorization policy",
                        )
                        sys.stdout.buffer.write(err)
                        sys.stdout.buffer.flush()
                else:
                    # Non-tool messages pass through immediately
                    self._write_to_server(raw_line)

        except (OSError, BrokenPipeError):
            logger.info("Claude Desktop closed the pipe, shutting down proxy")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def _forward_from_server(self) -> None:
        """Read from real server stdout and pipe straight to our stdout."""
        try:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        except (OSError, BrokenPipeError):
            pass

    def _relay_stderr(self) -> None:
        """Forward real server stderr → our stderr (visible in logs)."""
        try:
            for line in self._proc.stderr:  # type: ignore[union-attr]
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
        except (OSError, BrokenPipeError):
            pass

    # ------------------------------------------------------------------
    # Core interception
    # ------------------------------------------------------------------

    def _handle_tool_call(self, payload: Dict[str, Any], rpc_id: Any) -> bool:
        """
        Submit the tool call for user authorization.

        Returns True if the request should be forwarded, False if blocked.
        """
        tool_name, tool_params = _extract_tool_info(payload)
        logger.info(
            "Intercepted tools/call: tool=%s server=%s",
            tool_name,
            self.mcp_server_name,
        )

        request = MCPRequest(
            source=MCPRequestSource.CLAUDE_DESKTOP,
            mcp_server=self.mcp_server_name,
            tool_name=tool_name,
            tool_params=tool_params,
            raw_payload=payload,
        )

        try:
            if self.use_in_process_store and self.store is not None:
                self.store.add_request(request)
                status = self.store.wait_for_decision(
                    request.request_id,
                    timeout=settings.decision_timeout_seconds,
                )
            else:
                _submit_to_auth_server(request)
                status = _wait_for_decision_http(
                    request.request_id,
                    timeout=settings.decision_timeout_seconds,
                )
        except Exception as exc:
            logger.error("Authorization check failed: %s — blocking request", exc)
            return False

        if status == RequestStatus.ALLOWED:
            logger.info("Request %s ALLOWED", request.request_id)
            return True
        else:
            logger.info("Request %s %s", request.request_id, status.upper())
            return False

    def _write_to_server(self, data: bytes) -> None:
        try:
            self._proc.stdin.write(data)  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, BrokenPipeError):
            logger.warning("Cannot write to real MCP server stdin — server may have exited")


# ---------------------------------------------------------------------------
# CLI entry point (used when invoked directly by Claude Desktop)
# ---------------------------------------------------------------------------

def main(server_command: List[str], server_name: str = "mcp-server") -> None:
    """
    Entry point called by Claude Desktop via claude_desktop_config.json.

    Usage (from config):
        "command": "python",
        "args": ["-m", "src.proxy.stdio_proxy", "--server-name", "my-server",
                 "--", "node", "/path/to/real/mcp/server.js"]
    """
    proxy = StdioProxy(
        server_command=server_command,
        mcp_server_name=server_name,
        use_in_process_store=False,  # HTTP mode when invoked standalone
    )
    proxy.start()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP stdio authorization proxy")
    parser.add_argument("--server-name", default="mcp-server", help="Label for the MCP server")
    parser.add_argument("server_cmd", nargs="+", help="Command to start the real MCP server")
    args = parser.parse_args()

    main(server_command=args.server_cmd, server_name=args.server_name)
