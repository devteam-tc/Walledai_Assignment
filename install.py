"""
install.py — One-time setup helper.

What this script does
---------------------
1. Checks Python version compatibility.
2. Installs required packages.
3. Locates or creates Claude Desktop configuration file.
4. Patches claude_desktop_config.json to route each configured MCP server
   through this project's stdio proxy.
5. Creates a .env file with default settings.
6. Prints next-step instructions.

Run once:
    python install.py [--claude-config /path/to/claude_desktop_config.json]
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable

CLAUDE_CONFIG_PATHS = {
    "Windows":  Path(os.environ.get("APPDATA", "~")) / "Claude" / "claude_desktop_config.json",
    "Darwin":   Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    "Linux":    Path.home() / ".config" / "Claude" / "claude_desktop_config.json",
}


def _detect_config_path() -> Path:
    system = platform.system()
    return CLAUDE_CONFIG_PATHS.get(system, CLAUDE_CONFIG_PATHS["Linux"])


def _install_packages() -> None:
    print("\n📦 Installing Python dependencies …")
    req_file = PROJECT_ROOT / "requirements.txt"
    result = subprocess.run(
        [PYTHON, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("⚠  pip install failed — please run manually:")
        print(f"   pip install -r {req_file}")
    else:
        print("✅ Dependencies installed")


def _patch_claude_config(config_path: Path) -> None:
    """
    For every MCP server already in the config, insert the stdio proxy as
    an intermediate layer.  The original command is preserved as the
    wrapped server.
    """
    print(f"\n🔧 Patching Claude Desktop config: {config_path}")

    if not config_path.exists():
        print(f"   Config not found at {config_path}")
        print("   Creating a minimal example config …")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "mcpServers": {
                "example-server": {
                    "command": "python",
                    "args": [str(PROJECT_ROOT / "examples" / "demo_mcp_server.py")],
                }
            }
        }
        config_path.write_text(json.dumps(config, indent=2))
        print(f"   Created: {config_path}")

    config = json.loads(config_path.read_text())
    mcp_servers = config.get("mcpServers", {})

    proxy_script = str(PROJECT_ROOT / "main.py")
    patched = {}

    for name, server_cfg in mcp_servers.items():
        original_cmd = server_cfg.get("command", "")
        original_args = server_cfg.get("args", [])

        # Skip if already patched
        if proxy_script in original_args:
            print(f"   Skipping '{name}' — already patched")
            patched[name] = server_cfg
            continue

        # Build new config: python main.py --mode proxy --server-name <name> -- <original cmd> <args>
        new_cfg = dict(server_cfg)
        new_cfg["command"] = PYTHON
        new_cfg["args"] = (
            [proxy_script, "--mode", "proxy", "--server-name", name, "--"]
            + [original_cmd]
            + original_args
        )

        # Store original for reference
        new_cfg["_original_command"] = original_cmd
        new_cfg["_original_args"] = original_args

        patched[name] = new_cfg
        print(f"   Patched '{name}' → routed through MCP proxy")

    # Backup original
    backup = config_path.with_suffix(".json.bak")
    shutil.copy(config_path, backup)
    print(f"   Backup saved: {backup}")

    config["mcpServers"] = patched
    config_path.write_text(json.dumps(config, indent=2))
    print("✅ Claude Desktop config patched")


def _create_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        print("\n.env already exists — skipping")
        return
    env_content = """\
# MCP Monitor environment variables
MCP_AUTH_HOST=127.0.0.1
MCP_AUTH_PORT=8765
MCP_DECISION_TIMEOUT=120
MCP_MITMPROXY_PORT=8080
MCP_LOG_LEVEL=INFO
MCP_POPUP_ON_TOP=true
MCP_POPUP_POLL_MS=500
"""
    env_path.write_text(env_content)
    print(f"\n✅ Created .env at {env_path}")


def _create_demo_server() -> None:
    """Create a minimal demo MCP server for testing."""
    demo_dir = PROJECT_ROOT / "examples"
    demo_dir.mkdir(exist_ok=True)
    demo_path = demo_dir / "demo_mcp_server.py"
    if demo_path.exists():
        return

    code = '''\
"""Minimal MCP server for testing the authorization proxy."""
import json, sys

def respond(id, result):
    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":id,"result":result})+"\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method","")
    id = msg.get("id")
    if method == "initialize":
        respond(id, {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"demo","version":"1.0"}})
    elif method == "tools/list":
        respond(id, {"tools":[
            {"name":"read_file","description":"Read a file","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
            {"name":"write_file","description":"Write a file","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}},
        ]})
    elif method == "tools/call":
        tool = msg.get("params",{}).get("name","")
        respond(id, {"content":[{"type":"text","text":f"[DEMO] Tool {tool!r} executed successfully"}]})
    elif method == "notifications/initialized":
        pass  # no response needed
'''
    demo_path.write_text(code)
    print(f"✅ Demo MCP server created: {demo_path}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MCP Monitor installer")
    parser.add_argument(
        "--claude-config",
        default=None,
        help="Path to claude_desktop_config.json (auto-detected if not given)",
    )
    parser.add_argument(
        "--skip-pip", action="store_true",
        help="Skip pip install step",
    )
    parser.add_argument(
        "--skip-patch", action="store_true",
        help="Skip patching Claude Desktop config",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  MCP Communication Monitoring & Authorization System")
    print("  Setup / Installation Script")
    print("=" * 60)

    # Python version check
    if sys.version_info < (3, 10):
        print(f"❌ Python 3.10+ required (found {sys.version})")
        sys.exit(1)
    print(f"\n✅ Python {sys.version.split()[0]}")

    if not args.skip_pip:
        _install_packages()

    _create_env_file()
    _create_demo_server()

    if not args.skip_patch:
        config_path = Path(args.claude_config) if args.claude_config else _detect_config_path()
        _patch_claude_config(config_path)

    print("\n" + "=" * 60)
    print("  ✅ Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Start the monitor:  python main.py")
    print("  2. Restart Claude Desktop so it picks up the new config")
    print("  3. Use any MCP tool in Claude — the popup will appear!")
    print("\nFor Claude Web (browser) interception:")
    print("  1. Install mitmproxy:   pip install mitmproxy")
    print("  2. Run:  mitmdump -p 8080 -s src/proxy/http_proxy_addon.py")
    print("  3. Configure your browser to use proxy 127.0.0.1:8080")
    print("  4. Trust the mitmproxy CA cert (see docs/MITMPROXY_SETUP.md)")
    print()


if __name__ == "__main__":
    main()
