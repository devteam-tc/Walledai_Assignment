"""
Integration tests for the Authorization Server REST API.
Uses httpx AsyncClient against the FastAPI test client.
Run with:  pytest tests/test_authorization_server.py -v
"""

import pytest
from fastapi.testclient import TestClient

from src.core.authorization_server import app
from src.core.models import MCPRequestSource


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_submit_request(client):
    payload = {
        "source": MCPRequestSource.CLAUDE_DESKTOP,
        "mcp_server": "test-server",
        "tool_name": "read_file",
        "tool_params": {"path": "/etc/hosts"},
    }
    r = client.post("/request", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert "request_id" in body
    assert body["status"] == "pending"
    return body["request_id"]


def test_get_pending(client):
    # Submit two requests
    for tool in ("read_file", "write_file"):
        client.post("/request", json={
            "source": "claude_desktop",
            "mcp_server": "test",
            "tool_name": tool,
            "tool_params": {},
        })
    r = client.get("/pending")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2


def test_allow_decision(client):
    # Submit
    r = client.post("/request", json={
        "source": "claude_desktop",
        "mcp_server": "test",
        "tool_name": "get_weather",
        "tool_params": {"city": "Delhi"},
    })
    request_id = r.json()["request_id"]

    # Decide
    r2 = client.post("/decide", json={"request_id": request_id, "allow": True})
    assert r2.status_code == 200
    assert r2.json()["action"] == "ALLOWED"

    # Verify status
    r3 = client.get(f"/request/{request_id}")
    assert r3.json()["status"] == "allowed"


def test_block_decision(client):
    r = client.post("/request", json={
        "source": "claude_web",
        "mcp_server": "remote-server",
        "tool_name": "delete_file",
        "tool_params": {"path": "/important/file"},
    })
    request_id = r.json()["request_id"]

    r2 = client.post("/decide", json={"request_id": request_id, "allow": False})
    assert r2.status_code == 200
    assert r2.json()["action"] == "BLOCKED"

    r3 = client.get(f"/request/{request_id}")
    assert r3.json()["status"] == "blocked"


def test_duplicate_decision_rejected(client):
    r = client.post("/request", json={
        "source": "claude_desktop",
        "mcp_server": "test",
        "tool_name": "list_dir",
        "tool_params": {},
    })
    request_id = r.json()["request_id"]

    client.post("/decide", json={"request_id": request_id, "allow": True})
    r2 = client.post("/decide", json={"request_id": request_id, "allow": False})
    assert r2.status_code == 409


def test_unknown_request_404(client):
    r = client.get("/request/does-not-exist")
    assert r.status_code == 404
