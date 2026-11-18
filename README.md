# MCP Communication Monitoring & User Authorization System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-green?logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

**A Python application that monitors MCP communication from Claude Desktop and Claude Web, giving users per-request Allow/Block control via a real-time GUI popup.**

</div>

---

## ✨ Features

| Feature | Detail |
|---------|--------|
| 🔍 **Deep Interception** | Intercepts every `tools/call` JSON-RPC message before execution |
| 🖥 **Claude Desktop** | stdio man-in-the-middle proxy inserted between Claude Desktop and MCP servers |
| 🌐 **Claude Web** | mitmproxy HTTP/SSE addon intercepts browser-originated MCP requests |
| 🛡 **Always-on-top Popup** | Tkinter GUI floats above all windows; shows server, tool, and parameters |
| ⏱ **Blocking Gate** | Request is held pending until the user decides (configurable timeout) |
| 🔄 **Concurrent Requests** | Multiple simultaneous requests are queued and handled independently |
| 📋 **Audit Log** | All decisions logged to JSON file for later review |
| 🔌 **REST API** | Full FastAPI server so custom UIs or scripts can integrate |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Monitor System                           │
│                                                                 │
│  ┌──────────────┐  stdio   ┌──────────────┐  stdio  ┌────────┐ │
│  │ Claude       │◄────────►│ stdio_proxy  │◄───────►│  Real  │ │
│  │ Desktop      │  JSON-RPC│ .py          │ JSON-RPC│  MCP   │ │
│  └──────────────┘          └──────┬───────┘         │ Server │ │
│                                   │ HTTP             └────────┘ │
│  ┌──────────────┐  HTTP/SSE ┌─────┴──────────────────────────┐ │
│  │ Claude Web   │◄─────────►│      Authorization Server       │ │
│  │ (Browser)    │  via      │      (FastAPI / port 8765)      │ │
│  └──────────────┘  mitmproxy│                                 │ │
│                             │  POST /request  GET /pending    │ │
│  ┌──────────────┐           │  POST /decide   GET /health     │ │
│  │ http_proxy   │◄─────────►│                                 │ │
│  │ _addon.py    │           └──────────────┬──────────────────┘ │
│  │ (mitmproxy)  │                          │ poll every 500ms   │
│  └──────────────┘           ┌──────────────▼──────────────────┐ │
│                             │      GUI Popup (Tkinter)        │ │
│                             │  ┌─────────────────────────┐   │ │
│                             │  │  🔒 MCP Auth Request     │   │ │
│                             │  │  Server: filesystem      │   │ │
│                             │  │  Tool:   read_file       │   │ │
│                             │  │  Params: {path:"/etc/…"} │   │ │
│                             │  │  [✅ Allow]  [🚫 Block]  │   │ │
│                             │  └─────────────────────────┘   │ │
│                             └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **stdio Man-in-the-Middle (Claude Desktop)**  
   Claude Desktop communicates with MCP servers via stdin/stdout pipes over JSON-RPC 2.0. The proxy script acts as the MCP server to Claude while simultaneously acting as a client to the real server. `tools/call` messages are intercepted; all other messages pass through transparently.

2. **HTTP/SSE Addon via mitmproxy (Claude Web)**  
   Browser-based MCP traffic flows over HTTP. `mitmproxy` sits as a transparent proxy; the addon inspects request bodies for JSON-RPC `tools/call` payloads and blocks/releases flows based on user decisions.

3. **Thread-safe Request Store**  
   Each intercepted request is stored with a `threading.Event`. Proxy threads call `wait_for_decision()` which blocks on the Event. The GUI/API calls `record_decision()` which sets the Event, unblocking the proxy thread instantly.

4. **FastAPI REST Bridge**  
   The authorization server exposes a REST API (`POST /request`, `GET /pending`, `POST /decide`) so the GUI, the proxy, and any external tooling can communicate over localhost HTTP without shared memory.

5. **Fail-safe Defaults**  
   - If the auth server is unreachable → request is **blocked**
   - If decision timeout expires → request is **blocked**
   - If JSON-RPC parsing fails → message is forwarded (non-tool messages are always safe)

---

## 📁 Project Structure

```
walledai/
├── main.py                        # Application entry point
├── install.py                     # One-time setup + Claude Desktop config patcher
├── requirements.txt
├── .env                           # (created by install.py) runtime config
├── src/
│   ├── core/
│   │   ├── models.py              # Pydantic data models
│   │   ├── request_store.py       # Thread-safe pending request registry
│   │   └── authorization_server.py# FastAPI REST server
│   ├── proxy/
│   │   ├── stdio_proxy.py         # Claude Desktop stdio interceptor
│   │   └── http_proxy_addon.py    # mitmproxy addon for Claude Web
│   ├── ui/
│   │   └── popup_ui.py            # Tkinter always-on-top popup
│   └── utils/
│       ├── config.py              # Pydantic Settings (env vars + .env)
│       └── logger.py              # Structured logging
├── tests/
│   ├── test_request_store.py      # Unit tests for RequestStore
│   └── test_authorization_server.py # API integration tests
├── examples/
│   └── demo_mcp_server.py         # Minimal MCP server for testing
└── docs/
    └── MITMPROXY_SETUP.md         # Claude Web proxy setup guide
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Claude Desktop** installed (for Desktop interception)
- **Google Chrome** (for Web interception)

### 1 — Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/walled-mcp-monitor.git
cd walled-mcp-monitor
pip install -r requirements.txt
```

### 2 — Run the Setup Script

```bash
python install.py
```

This will:
- Install all dependencies
- Create a `.env` file with defaults
- Create a demo MCP server for testing
- Automatically patch `claude_desktop_config.json` to route every configured MCP server through the proxy

### 3 — Start the Monitor

```bash
python main.py
```

This starts the **authorization server** (port 8765) and the **GUI popup** simultaneously.

### 4 — Restart Claude Desktop

Claude Desktop must be fully restarted so it picks up the patched config and spawns the proxy as the MCP server process.

### 5 — Use an MCP Tool

Ask Claude to use any MCP tool. The popup will appear immediately asking for your decision.

---

## 🌐 Claude Web Interception Setup

Claude Web uses HTTP/SSE transport. See **[docs/MITMPROXY_SETUP.md](docs/MITMPROXY_SETUP.md)** for the full guide.

**Quick version:**

```bash
# Install mitmproxy (if not already)
pip install mitmproxy

# Start auth server + GUI first
python main.py &

# Run mitmproxy with the addon
mitmdump -p 8080 -s src/proxy/http_proxy_addon.py
```

Then configure Chrome to use `127.0.0.1:8080` as its HTTP proxy and trust the mitmproxy CA certificate.

---

## ⚙️ Configuration

All settings live in `.env` (or environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUTH_HOST` | `127.0.0.1` | Authorization server host |
| `MCP_AUTH_PORT` | `8765` | Authorization server port |
| `MCP_DECISION_TIMEOUT` | `120` | Seconds before auto-block on no decision |
| `MCP_MITMPROXY_PORT` | `8080` | mitmproxy listening port |
| `MCP_LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`/`INFO`/`WARNING`) |
| `MCP_POPUP_ON_TOP` | `true` | Keep popup always above other windows |
| `MCP_POPUP_POLL_MS` | `500` | GUI polling interval in milliseconds |

---

## 🧪 Running Tests

```bash
pip install pytest
pytest tests/ -v
```

Expected output:
```
tests/test_request_store.py::TestRequestStore::test_add_and_retrieve     PASSED
tests/test_request_store.py::TestRequestStore::test_allow_decision        PASSED
tests/test_request_store.py::TestRequestStore::test_block_decision        PASSED
tests/test_request_store.py::TestRequestStore::test_concurrent_requests   PASSED
tests/test_request_store.py::TestRequestStore::test_timeout               PASSED
tests/test_authorization_server.py::test_health                           PASSED
tests/test_authorization_server.py::test_submit_request                   PASSED
tests/test_authorization_server.py::test_allow_decision                   PASSED
tests/test_authorization_server.py::test_block_decision                   PASSED
...
```

---

## 🔌 REST API Reference

The authorization server exposes these endpoints at `http://127.0.0.1:8765`:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `POST` | `/request` | Submit an intercepted MCP request |
| `GET` | `/pending` | List all requests awaiting user decision |
| `GET` | `/request/{id}` | Get a single request by ID |
| `POST` | `/decide` | Record allow/block decision |
| `GET` | `/requests/all` | Full audit log (last 100) |

Interactive Swagger docs: **http://127.0.0.1:8765/docs**

---

## 🛡 Security Considerations

- The authorization server binds to `127.0.0.1` only — not reachable from the network.
- mitmproxy CA cert is local to your machine and should be treated as sensitive.
- Blocked requests return a JSON-RPC error; the real MCP server never executes them.
- Timeout is fail-safe: unresponded requests are **blocked**, not allowed.
- Logs are written to `~/.mcp_monitor/mcp_monitor.log`; rotate as needed.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🎥 Demo

> See the [demo video](https://youtu.be/YOUR_VIDEO_LINK) for a walkthrough of:
> - Successful request approval (Allow flow)
> - Request blocking (Block flow)
> - Concurrent requests handling
> - Claude Web interception via mitmproxy
