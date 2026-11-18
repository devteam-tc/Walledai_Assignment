# mitmproxy Setup for Claude Web Interception

This guide walks you through configuring mitmproxy to intercept Claude Web MCP traffic.

## Overview

Claude Web (claude.ai) communicates with remote MCP servers over HTTPS using either:
- **Streamable HTTP** — `POST /mcp` with JSON-RPC body
- **Legacy SSE** — `POST /messages` + `GET /sse`

mitmproxy acts as a transparent HTTPS proxy that decrypts, inspects, and optionally blocks these requests.

---

## Step 1 — Install mitmproxy

```bash
pip install mitmproxy
```

Verify:
```bash
mitmdump --version
```

---

## Step 2 — Run the MCP Auth Addon

```bash
# Start the main application first (auth server + GUI)
python main.py

# In a second terminal, run mitmproxy with the addon
mitmdump -p 8080 -s src/proxy/http_proxy_addon.py --ssl-insecure
```

Flags:
- `-p 8080` — listen on port 8080
- `-s src/proxy/http_proxy_addon.py` — load our interception addon
- `--ssl-insecure` — required for some MCP server TLS configurations

---

## Step 3 — Configure Your Browser Proxy

### Chrome / Edge (Windows)
1. Settings → System → Open your computer's proxy settings
2. Manual proxy setup → HTTP proxy: `127.0.0.1`, Port: `8080`
3. Apply to HTTPS too

### Chrome via Command Line
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --proxy-server="http://127.0.0.1:8080" ^
  --ignore-certificate-errors-spki-list=...
```

---

## Step 4 — Trust the mitmproxy CA Certificate

mitmproxy generates a local CA certificate to decrypt HTTPS. You must trust it.

1. With mitmproxy running, visit: **http://mitm.it**
2. Download and install the certificate for your OS/browser:
   - **Windows**: Double-click → Install Certificate → Local Machine → Trusted Root CAs
   - **macOS**: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.mitmproxy/mitmproxy-ca-cert.pem`
   - **Linux (Chrome)**: `certutil -d sql:$HOME/.pki/nssdb -A -t "CT,C,C" -n mitmproxy -i ~/.mitmproxy/mitmproxy-ca-cert.pem`

---

## Step 5 — Test It

1. Open claude.ai in the browser
2. Connect an MCP server and ask Claude to use a tool
3. The MCP Monitor popup should appear immediately

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `SSL: CERTIFICATE_VERIFY_FAILED` | Re-install mitmproxy CA cert |
| Popup doesn't appear | Ensure `python main.py` is running and check `~/.mcp_monitor/mcp_monitor.log` |
| mitmproxy shows no traffic | Check browser proxy settings; some browsers ignore system proxy |
| `Connection refused` to auth server | Make sure port 8765 is not firewalled |

---

## Network Flow Diagram

```
Browser (Claude Web)
        │  HTTPS request to MCP server
        ▼
mitmproxy (127.0.0.1:8080)
        │  Decrypts TLS → inspects JSON-RPC
        │  tools/call detected?
        │    YES → POST /request to auth server (port 8765)
        │           wait for decision
        │    NO  → forward immediately
        │
        ├── ALLOW → re-encrypt → forward to real MCP server
        └── BLOCK → return HTTP 403 JSON-RPC error
```
