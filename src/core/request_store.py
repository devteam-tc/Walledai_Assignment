"""
Thread-safe in-memory store for pending MCP authorization requests.

Each request is stored with a threading.Event so that the proxy thread
can block waiting for a user decision without busy-polling.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from src.core.models import MCPRequest, AuthorizationDecision, RequestStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RequestStore:
    """
    Central registry for all pending MCP authorization requests.

    Thread-safe design:
    - A dict maps request_id → (MCPRequest, threading.Event)
    - The proxy thread calls `wait_for_decision()` which blocks on the Event
    - The UI/API thread calls `record_decision()` which sets the Event
    """

    def __init__(self, default_timeout: float = 120.0):
        self._lock = threading.Lock()
        # request_id → (MCPRequest, Event, decision_holder)
        self._store: Dict[str, Tuple[MCPRequest, threading.Event, list]] = {}
        self.default_timeout = default_timeout

    # ------------------------------------------------------------------
    # Write path (proxy threads)
    # ------------------------------------------------------------------

    def add_request(self, request: MCPRequest) -> None:
        """Register a new pending request and allocate its Event gate."""
        event = threading.Event()
        decision_holder: list = []  # mutable container so threads can share
        with self._lock:
            self._store[request.request_id] = (request, event, decision_holder)
        logger.info(
            "Registered pending request",
            extra={
                "request_id": request.request_id,
                "tool": request.tool_name,
                "server": request.mcp_server,
                "source": request.source,
            },
        )

    def wait_for_decision(
        self,
        request_id: str,
        timeout: Optional[float] = None,
    ) -> RequestStatus:
        """
        Block the calling thread until a decision arrives or timeout fires.

        Returns the final RequestStatus (ALLOWED / BLOCKED / TIMEOUT / ERROR).
        """
        timeout = timeout or self.default_timeout
        with self._lock:
            entry = self._store.get(request_id)

        if entry is None:
            logger.error("wait_for_decision: unknown request_id %s", request_id)
            return RequestStatus.ERROR

        request, event, decision_holder = entry
        logger.debug("Proxy blocking on request %s (timeout=%.1fs)", request_id, timeout)

        signalled = event.wait(timeout=timeout)

        if not signalled:
            logger.warning("Request %s timed out after %.1fs", request_id, timeout)
            self._finalize(request_id, RequestStatus.TIMEOUT)
            return RequestStatus.TIMEOUT

        # Decision was recorded; read it back
        with self._lock:
            if decision_holder:
                return decision_holder[0]
            return RequestStatus.ERROR

    # ------------------------------------------------------------------
    # Read / write path (API / UI threads)
    # ------------------------------------------------------------------

    def get_pending(self) -> list[MCPRequest]:
        """Return all requests still in PENDING status."""
        with self._lock:
            return [
                req
                for req, _, __ in self._store.values()
                if req.status == RequestStatus.PENDING
            ]

    def get_request(self, request_id: str) -> Optional[MCPRequest]:
        """Retrieve a request by ID (any status)."""
        with self._lock:
            entry = self._store.get(request_id)
            return entry[0] if entry else None

    def record_decision(self, decision: AuthorizationDecision) -> bool:
        """
        Apply an allow/block decision to the pending request.

        Returns True if the decision was applied successfully.
        """
        with self._lock:
            entry = self._store.get(decision.request_id)
            if entry is None:
                logger.warning("Decision for unknown request %s ignored", decision.request_id)
                return False
            request, event, decision_holder = entry
            if request.status != RequestStatus.PENDING:
                logger.warning(
                    "Request %s already decided (%s), ignoring duplicate",
                    decision.request_id,
                    request.status,
                )
                return False

            new_status = RequestStatus.ALLOWED if decision.allow else RequestStatus.BLOCKED
            request.status = new_status
            request.decided_at = decision.decided_at
            decision_holder.append(new_status)

        event.set()  # unblock the proxy thread
        logger.info(
            "Decision recorded",
            extra={
                "request_id": decision.request_id,
                "decision": "ALLOWED" if decision.allow else "BLOCKED",
            },
        )
        return True

    def _finalize(self, request_id: str, status: RequestStatus) -> None:
        with self._lock:
            entry = self._store.get(request_id)
            if entry:
                entry[0].status = status
                decision_holder = entry[2]
                if not decision_holder:
                    decision_holder.append(status)

    def cleanup_decided(self, max_age_seconds: float = 300.0) -> int:
        """Remove old decided requests to prevent unbounded memory growth."""
        cutoff = time.time() - max_age_seconds
        removed = 0
        with self._lock:
            to_remove = []
            for rid, (req, _, __) in self._store.items():
                if req.status != RequestStatus.PENDING:
                    if req.decided_at is not None:
                        decided_ts = req.decided_at.timestamp()
                        if decided_ts < cutoff:
                            to_remove.append(rid)
            for rid in to_remove:
                del self._store[rid]
                removed += 1
        if removed:
            logger.debug("Cleaned up %d decided requests", removed)
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
