"""
mitmproxy addon for intercepting Claude Web MCP HTTP/SSE requests.

Architecture
------------
Claude Web (browser) communicates with MCP servers over HTTP using either:
  • Streamable HTTP  (POST /mcp  with application/json body)
  • Legacy SSE       (POST /messages + GET /sse)

This addon runs inside mitmproxy and intercepts those requests.
When a ``tools/call`` JSON-RPC payload is detected the addon:
  1. Submits it to the authorization server.
  2. Suspends the HTTP flow (mitmproxy supports native flow suspension).
  3. Resumes or kills the flow based on the user's decision.

Setup
-----
Run mitmproxy with this addon:

    mitmdump -p 8080 --mode transparent -s src/proxy/http_proxy_addon.py

Then configure your browser (or system proxy) to route traffic through
127.0.0.1:8080.  For HTTPS you'll need to trust the mitmproxy CA cert.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

try:
    import mitmproxy.http as mhttp
    from mitmproxy import ctx
except ImportError:
    # Allow module to be imported without mitmproxy for unit-testing purposes
    mhttp = None  # type: ignore[assignment]
    ctx = None    # type: ignore[assignment]

from src.core.models import MCPRequest, MCPRequestSource, RequestStatus
from src.utils.config import settings
from src.utils.logger import configure_logging, get_logger

configure_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TOOLS_CALL_METHOD = "tools/call"

_MCP_PATH_PATTERNS = ("/mcp", "/messages", "/sse", "/rpc")


def _is_mcp_request(flow) -> bool:  # type: ignore[no-untyped-def]
    """Heuristic to decide whether this HTTP request carries MCP traffic."""
    if mhttp is None:
        return False
    path = urlparse(flow.request.url).path.lower()
    if not any(path.endswith(p) or p in path for p in _MCP_PATH_PATTERNS):
        return False
    ct = flow.request.headers.get("content-type", "")
    return "json" in ct or "event-stream" in ct


def _extract_jsonrpc(body: bytes) -> Optional[Dict[str, Any]]:
    """Try to parse the request body as JSON-RPC."""
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and "jsonrpc" in payload:
            return payload
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _server_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.hostname or "unknown-server"


def _submit_and_wait(request: MCPRequest) -> RequestStatus:
    """Submit to auth server and poll until a decision arrives."""
    url = f"{settings.auth_server_url}/request"
    try:
        resp = httpx.post(
            url,
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Could not submit to auth server: %s", exc)
        return RequestStatus.BLOCKED  # fail-safe: block if we can't reach server

    # Poll for decision
    import time
    deadline = time.monotonic() + settings.decision_timeout_seconds
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                f"{settings.auth_server_url}/request/{request.request_id}",
                timeout=3.0,
            )
            if r.status_code == 200:
                st = r.json().get("status", "pending")
                if st != "pending":
                    return RequestStatus(st)
        except Exception:
            pass
        time.sleep(0.3)
    return RequestStatus.TIMEOUT


def _build_blocked_response() -> bytes:
    """Return a JSON-RPC error body for blocked requests."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32603,
            "message": "Request blocked by MCP Authorization Monitor",
        },
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# mitmproxy Addon
# ---------------------------------------------------------------------------

class MCPAuthAddon:
    """
    mitmproxy addon that intercepts MCP tool calls and gates them on
    user authorization.
    """

    def __init__(self) -> None:
        logger.info("MCPAuthAddon initialised")

    # ------------------------------------------------------------------
    # mitmproxy hook
    # ------------------------------------------------------------------

    def request(self, flow: "mhttp.HTTPFlow") -> None:  # type: ignore[name-defined]
        """Called by mitmproxy for every HTTP request before it is forwarded."""
        if not _is_mcp_request(flow):
            return

        body = flow.request.get_content()
        if not body:
            return

        payload = _extract_jsonrpc(body)
        if payload is None:
            return

        method = payload.get("method", "")
        if method != TOOLS_CALL_METHOD:
            return

        # --- Intercept tools/call ---
        params = payload.get("params", {})
        tool_name = params.get("name", "<unknown>")
        tool_params = params.get("arguments", params.get("input", {}))
        server_name = _server_name_from_url(flow.request.url)

        logger.info(
            "HTTP intercept tools/call: tool=%s server=%s url=%s",
            tool_name,
            server_name,
            flow.request.url,
        )

        request_obj = MCPRequest(
            source=MCPRequestSource.CLAUDE_WEB,
            mcp_server=server_name,
            tool_name=tool_name,
            tool_params=tool_params,
            raw_payload=payload,
        )

        # Submit to auth server and wait (blocking — mitmproxy calls this
        # synchronously; the flow is naturally suspended while we block).
        status = _submit_and_wait(request_obj)

        if status == RequestStatus.ALLOWED:
            logger.info("HTTP request %s ALLOWED", request_obj.request_id)
            # Flow proceeds normally — no action needed
        else:
            logger.info("HTTP request %s BLOCKED (%s)", request_obj.request_id, status)
            # Short-circuit: reply with error, do not forward to real server
            flow.response = mhttp.Response.make(  # type: ignore[attr-defined]
                403,
                _build_blocked_response(),
                {"Content-Type": "application/json"},
            )

    def response(self, flow: "mhttp.HTTPFlow") -> None:  # type: ignore[name-defined]
        """Log MCP responses for audit purposes (non-blocking)."""
        if not _is_mcp_request(flow):
            return
        logger.debug(
            "MCP response: status=%d url=%s",
            flow.response.status_code,
            flow.request.url,
        )


# mitmproxy discovers addons via this module-level name
addons = [MCPAuthAddon()]
