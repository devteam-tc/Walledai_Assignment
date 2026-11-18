"""
Minimal MCP server for testing the authorization proxy.

This server implements a small subset of the MCP protocol:
  - initialize handshake
  - tools/list
  - tools/call  (simulated — does not touch the real filesystem)

It is intended purely for demonstrating the proxy interception.
"""

import json
import sys
from typing import Any, Dict


def _send(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _respond(id: Any, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": id, "result": result})


def _error(id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List the contents of a directory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "execute_command",
        "description": "Execute a shell command",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
        },
    },
]


def handle_message(msg: Dict[str, Any]) -> None:
    method = msg.get("method", "")
    id_ = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        _respond(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "demo-server", "version": "1.0.0"},
        })

    elif method == "notifications/initialized":
        pass  # No response required for notifications

    elif method == "tools/list":
        _respond(id_, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        # Simulate tool execution
        if tool_name == "read_file":
            path = tool_args.get("path", "")
            _respond(id_, {
                "content": [{"type": "text", "text": f"[DEMO] File contents of {path!r}\nLine 1\nLine 2\nLine 3"}]
            })
        elif tool_name == "write_file":
            path = tool_args.get("path", "")
            _respond(id_, {
                "content": [{"type": "text", "text": f"[DEMO] Successfully wrote to {path!r}"}]
            })
        elif tool_name == "list_directory":
            path = tool_args.get("path", "")
            _respond(id_, {
                "content": [{"type": "text", "text": f"[DEMO] Contents of {path!r}:\n- file1.txt\n- file2.py\n- subdir/"}]
            })
        elif tool_name == "execute_command":
            cmd = tool_args.get("command", "")
            _respond(id_, {
                "content": [{"type": "text", "text": f"[DEMO] Command {cmd!r} executed\nOutput: success"}]
            })
        else:
            _error(id_, -32601, f"Unknown tool: {tool_name!r}")

    elif method == "ping":
        _respond(id_, {})

    else:
        # Unknown method — return method-not-found
        if id_ is not None:  # Only error for requests (not notifications)
            _error(id_, -32601, f"Method not found: {method!r}")


def main() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
            handle_message(msg)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"[demo-server] JSON parse error: {exc}\n")
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"[demo-server] Error: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
