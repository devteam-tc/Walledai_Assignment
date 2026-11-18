"""
Pydantic data models for MCP request/response objects.
All shared types used across proxy, server, and UI components.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_serializer


class RequestStatus(str, Enum):
    """Lifecycle states of a pending MCP authorization request."""
    PENDING = "pending"
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    ERROR = "error"


class MCPRequestSource(str, Enum):
    """Origin of the intercepted MCP request."""
    CLAUDE_DESKTOP = "claude_desktop"
    CLAUDE_WEB = "claude_web"
    UNKNOWN = "unknown"


class MCPRequest(BaseModel):
    """Represents a single intercepted MCP tool invocation request."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier assigned by the proxy at interception time."""

    source: MCPRequestSource = MCPRequestSource.UNKNOWN
    """Which Claude client originated this request."""

    mcp_server: str = ""
    """Name / identifier of the MCP server being called (from config or URL)."""

    tool_name: str = ""
    """The MCP tool being invoked (from tools/call method)."""

    tool_params: Dict[str, Any] = Field(default_factory=dict)
    """Raw parameters passed to the tool, extracted from JSON-RPC."""

    raw_payload: Optional[Dict[str, Any]] = None
    """Full original JSON-RPC payload for complete transparency."""

    intercepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """UTC timestamp when the proxy intercepted the request."""

    status: RequestStatus = RequestStatus.PENDING
    """Current authorization status."""

    decided_at: Optional[datetime] = None
    """UTC timestamp when the user made their decision."""

    @field_serializer("intercepted_at", "decided_at")
    def _serialize_dt(self, v: Optional[datetime]) -> Optional[str]:
        return v.isoformat() if v else None


class AuthorizationDecision(BaseModel):
    """User's allow/block decision for a pending request."""

    request_id: str
    allow: bool
    """True → allow the request; False → block it."""

    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingRequestResponse(BaseModel):
    """API response payload listing all currently pending requests."""

    requests: list[MCPRequest]
    total: int


class HealthResponse(BaseModel):
    """Health-check response from the authorization server."""

    status: str = "ok"
    version: str = "1.0.0"
    pending_count: int = 0
