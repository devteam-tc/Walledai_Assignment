"""
Unit tests for the RequestStore.
Run with:  pytest tests/ -v
"""

import threading
import time

import pytest

from src.core.models import AuthorizationDecision, MCPRequest, MCPRequestSource, RequestStatus
from src.core.request_store import RequestStore


def _make_request(**kwargs) -> MCPRequest:
    defaults = dict(
        source=MCPRequestSource.CLAUDE_DESKTOP,
        mcp_server="test-server",
        tool_name="read_file",
        tool_params={"path": "/tmp/test.txt"},
    )
    defaults.update(kwargs)
    return MCPRequest(**defaults)


# ---------------------------------------------------------------------------
# Basic lifecycle
# ---------------------------------------------------------------------------

class TestRequestStore:

    def test_add_and_retrieve(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)
        retrieved = store.get_request(req.request_id)
        assert retrieved is not None
        assert retrieved.request_id == req.request_id
        assert retrieved.status == RequestStatus.PENDING

    def test_pending_list(self):
        store = RequestStore()
        r1 = _make_request()
        r2 = _make_request(tool_name="write_file")
        store.add_request(r1)
        store.add_request(r2)
        pending = store.get_pending()
        assert len(pending) == 2

    def test_allow_decision(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)

        result_holder = []

        def waiter():
            status = store.wait_for_decision(req.request_id, timeout=5.0)
            result_holder.append(status)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)

        decision = AuthorizationDecision(request_id=req.request_id, allow=True)
        success = store.record_decision(decision)
        t.join(timeout=5.0)

        assert success
        assert result_holder == [RequestStatus.ALLOWED]
        assert store.get_request(req.request_id).status == RequestStatus.ALLOWED

    def test_block_decision(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)

        result_holder = []

        def waiter():
            status = store.wait_for_decision(req.request_id, timeout=5.0)
            result_holder.append(status)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)

        decision = AuthorizationDecision(request_id=req.request_id, allow=False)
        store.record_decision(decision)
        t.join(timeout=5.0)

        assert result_holder == [RequestStatus.BLOCKED]

    def test_timeout(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)

        status = store.wait_for_decision(req.request_id, timeout=0.5)
        assert status == RequestStatus.TIMEOUT

    def test_duplicate_decision_ignored(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)

        result_holder = []

        def waiter():
            status = store.wait_for_decision(req.request_id, timeout=5.0)
            result_holder.append(status)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)

        d1 = AuthorizationDecision(request_id=req.request_id, allow=True)
        d2 = AuthorizationDecision(request_id=req.request_id, allow=False)

        store.record_decision(d1)
        result2 = store.record_decision(d2)  # should be rejected
        t.join(timeout=5.0)

        assert result2 is False
        assert result_holder == [RequestStatus.ALLOWED]

    def test_concurrent_requests(self):
        """Multiple simultaneous pending requests resolved independently."""
        store = RequestStore(default_timeout=10.0)
        requests = [_make_request() for _ in range(5)]
        results = {}

        def wait_for(req):
            store.add_request(req)
            status = store.wait_for_decision(req.request_id, timeout=5.0)
            results[req.request_id] = status

        threads = [threading.Thread(target=wait_for, args=(r,)) for r in requests]
        for t in threads:
            t.start()
        time.sleep(0.2)

        # Allow even-indexed, block odd-indexed
        for i, req in enumerate(requests):
            decision = AuthorizationDecision(request_id=req.request_id, allow=(i % 2 == 0))
            store.record_decision(decision)

        for t in threads:
            t.join(timeout=6.0)

        for i, req in enumerate(requests):
            expected = RequestStatus.ALLOWED if i % 2 == 0 else RequestStatus.BLOCKED
            assert results[req.request_id] == expected, f"Request {i} wrong status"

    def test_cleanup(self):
        store = RequestStore()
        req = _make_request()
        store.add_request(req)
        decision = AuthorizationDecision(request_id=req.request_id, allow=True)
        store.record_decision(decision)

        # Cleanup with 0 max age should remove it
        removed = store.cleanup_decided(max_age_seconds=0)
        assert removed == 1
        assert len(store) == 0

    def test_unknown_request_id(self):
        store = RequestStore()
        status = store.wait_for_decision("non-existent-id", timeout=0.1)
        assert status == RequestStatus.ERROR
