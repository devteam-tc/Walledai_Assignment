"""Demonstrate Allow and Block decisions via the REST API."""
import httpx
import sys

BASE = "http://127.0.0.1:8765"

# Get current pending
pending = httpx.get(f"{BASE}/pending").json()["requests"]
if not pending:
    print("No pending requests. Run send_test_requests.py first.")
    sys.exit(1)

print(f"Found {len(pending)} pending request(s)\n")
for req in pending:
    print(f"  [{req['source']}] {req['mcp_server']} → {req['tool_name']}  (id: {req['request_id'][:8]}...)")

# ALLOW the first request
first_id = pending[0]["request_id"]
r = httpx.post(f"{BASE}/decide", json={"request_id": first_id, "allow": True})
print(f"\n✅ ALLOWED  [{pending[0]['tool_name']}] → {r.json()['action']}")

# BLOCK the second request (if exists)
if len(pending) >= 2:
    second_id = pending[1]["request_id"]
    r = httpx.post(f"{BASE}/decide", json={"request_id": second_id, "allow": False})
    print(f"🚫 BLOCKED  [{pending[1]['tool_name']}] → {r.json()['action']}")

# Show what remains pending
remaining = httpx.get(f"{BASE}/pending").json()
print(f"\nRemaining pending: {remaining['total']}")

# Show full audit log
all_reqs = httpx.get(f"{BASE}/requests/all").json()
print("\nAudit log:")
for r in all_reqs:
    icon = {"allowed": "✅", "blocked": "🚫", "pending": "⏳"}.get(r["status"], "?")
    print(f"  {icon} {r['tool_name']:20s}  status={r['status']:8s}  server={r['mcp_server']}")
