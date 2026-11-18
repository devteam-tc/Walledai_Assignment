"""Send simulated MCP requests to trigger the popup UI."""
import httpx

requests_to_send = [
    {
        "source": "claude_desktop",
        "mcp_server": "filesystem",
        "tool_name": "read_file",
        "tool_params": {"path": "C:/Users/BayyaDivyaSri/Documents/secret_notes.txt"},
    },
    {
        "source": "claude_desktop",
        "mcp_server": "filesystem",
        "tool_name": "write_file",
        "tool_params": {"path": "C:/Users/BayyaDivyaSri/Desktop/output.txt", "content": "Hello from Claude"},
    },
    {
        "source": "claude_web",
        "mcp_server": "api.example.com",
        "tool_name": "execute_command",
        "tool_params": {"command": "git", "args": ["push", "origin", "main"]},
    },
]

ids = []
for req in requests_to_send:
    r = httpx.post("http://127.0.0.1:8765/request", json=req, timeout=5)
    rid = r.json()["request_id"]
    ids.append(rid)
    print(f"Submitted [{req['tool_name']}] → ID: {rid[:8]}...")

pending = httpx.get("http://127.0.0.1:8765/pending").json()
print(f"\nTotal pending cards shown in popup: {pending['total']}")
print("\nRequest IDs for manual decision testing:")
for rid in ids:
    print(f"  {rid}")
