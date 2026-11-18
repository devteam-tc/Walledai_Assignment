"""
FastAPI authorization server.

This is the central hub of the system:
  - Proxy components POST intercepted MCP requests here.
  - The GUI polls GET /pending to build its queue.
  - The GUI or CLI POSTs to /decide to allow or block a request.
  - Proxy components block on the threading.Event inside RequestStore
    until a decision arrives, then continue or abort the request.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.models import (
    AuthorizationDecision,
    HealthResponse,
    MCPRequest,
    PendingRequestResponse,
    RequestStatus,
)
from src.core.request_store import RequestStore
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared state (injected into the FastAPI app via app.state)
# ---------------------------------------------------------------------------
_store: Optional[RequestStore] = None


def get_store() -> RequestStore:
    if _store is None:
        raise RuntimeError("RequestStore not initialised")
    return _store


# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------
def _cleanup_loop(store: RequestStore, interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            removed = store.cleanup_decided()
            if removed:
                logger.debug("Background cleanup removed %d old requests", removed)
        except Exception as exc:  # noqa: BLE001
            logger.error("Cleanup error: %s", exc)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _store
    _store = RequestStore(default_timeout=settings.decision_timeout_seconds)
    app.state.store = _store
    logger.info("Authorization server starting on %s:%d", settings.auth_server_host, settings.auth_server_port)

    # Start background cleanup thread
    t = threading.Thread(
        target=_cleanup_loop,
        args=(_store, settings.store_cleanup_interval_seconds),
        daemon=True,
        name="store-cleanup",
    )
    t.start()

    yield

    logger.info("Authorization server shutting down")


app = FastAPI(
    title="MCP Authorization Server",
    description="Intercepts MCP requests and gates them on user approval",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    """Liveness probe used by proxies to verify the server is ready."""
    store = app.state.store
    return HealthResponse(pending_count=len(store.get_pending()))


@app.post("/request", status_code=status.HTTP_201_CREATED, tags=["Proxy"])
async def submit_request(request: MCPRequest) -> dict:
    """
    Called by proxy components when they intercept an MCP tool call.

    The proxy should submit the request and then call GET /wait/{request_id}
    (or use the in-process RequestStore.wait_for_decision) to block until
    a decision is recorded.
    """
    store = app.state.store
    store.add_request(request)
    logger.info("Received request %s from %s", request.request_id, request.source)
    return {"request_id": request.request_id, "status": "pending"}


@app.get("/pending", response_model=PendingRequestResponse, tags=["UI"])
async def get_pending() -> PendingRequestResponse:
    """Return all requests awaiting user decision (polled by the GUI)."""
    store = app.state.store
    pending = store.get_pending()
    return PendingRequestResponse(requests=pending, total=len(pending))


@app.get("/request/{request_id}", tags=["UI"])
async def get_request(request_id: str) -> MCPRequest:
    """Retrieve a single request by ID regardless of its status."""
    store = app.state.store
    req = store.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Request {request_id!r} not found")
    return req


@app.post("/decide", tags=["UI"])
async def decide(decision: AuthorizationDecision) -> dict:
    """
    Record the user's allow/block decision.

    This unblocks any proxy thread waiting on the corresponding request.
    """
    store = app.state.store
    success = store.record_decision(decision)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request {decision.request_id!r} not found or already decided",
        )
    action = "ALLOWED" if decision.allow else "BLOCKED"
    logger.info("Request %s → %s", decision.request_id, action)
    return {"request_id": decision.request_id, "action": action}


@app.get("/requests/all", tags=["UI"])
async def get_all_requests(limit: int = 100) -> list:
    """Return all tracked requests (pending + decided) for audit log view."""
    store = app.state.store
    with store._lock:
        all_reqs = [req for req, _, __ in store._store.values()]
    all_reqs.sort(key=lambda r: r.intercepted_at, reverse=True)
    return all_reqs[:limit]


# ---------------------------------------------------------------------------
# Server runner (called from main.py)
# ---------------------------------------------------------------------------

def run_server(store: Optional[RequestStore] = None) -> None:
    """Launch the uvicorn server (blocking call — run in a thread)."""
    global _store
    if store is not None:
        _store = store

    uvicorn.run(
        app,
        host=settings.auth_server_host,
        port=settings.auth_server_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
