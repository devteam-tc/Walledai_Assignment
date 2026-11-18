"""
Tkinter-based authorization popup UI.

Design
------
• Always-on-top window that appears whenever a new MCP request is pending.
• Polls GET /pending on the authorization server every 500 ms.
• Each pending request gets its own card with Allow / Block buttons.
• Multiple simultaneous requests are handled via a scrollable queue.
• The popup auto-hides when the queue is empty and re-appears when new
  requests arrive (no need to keep it visible constantly).

The UI intentionally does NOT block the main thread — it uses Tkinter's
``after()`` callback mechanism for safe cross-thread GUI updates.
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict, List, Optional

import httpx

from src.core.models import AuthorizationDecision, MCPRequest, RequestStatus
from src.utils.config import settings
from src.utils.logger import configure_logging, get_logger

configure_logging(level=settings.log_level, log_file=settings.log_file)
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
BG_DARK      = "#1e1e2e"
BG_CARD      = "#2a2a3e"
BG_CARD_HDR  = "#313148"
FG_MAIN      = "#cdd6f4"
FG_DIM       = "#a6adc8"
FG_TOOL      = "#89b4fa"
FG_SERVER    = "#a6e3a1"
FG_WARN      = "#f38ba8"
BTN_ALLOW    = "#a6e3a1"
BTN_BLOCK    = "#f38ba8"
BTN_FG       = "#1e1e2e"
ACCENT       = "#cba6f7"

# ---------------------------------------------------------------------------
# Request card widget
# ---------------------------------------------------------------------------

class RequestCard(tk.Frame):
    """
    Visual card representing a single pending MCP request.

    Shows:
      • Server name, tool name, timestamp
      • Parameters (truncated for readability)
      • Allow / Block buttons
    """

    def __init__(self, parent: tk.Widget, request: MCPRequest, on_decide_cb):  # type: ignore[type-arg]
        super().__init__(parent, bg=BG_CARD, padx=12, pady=10,
                         highlightthickness=1, highlightbackground=ACCENT)
        self.request = request
        self.on_decide = on_decide_cb
        self._build()

    def _build(self) -> None:
        # Header row
        hdr = tk.Frame(self, bg=BG_CARD_HDR, padx=8, pady=5)
        hdr.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            hdr, text="🔒 MCP Authorization Request",
            bg=BG_CARD_HDR, fg=ACCENT,
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT)

        ts = self.request.intercepted_at.strftime("%H:%M:%S")
        tk.Label(
            hdr, text=ts, bg=BG_CARD_HDR, fg=FG_DIM,
            font=("Segoe UI", 9),
        ).pack(side=tk.RIGHT)

        # Source badge
        source_text = "🖥 Claude Desktop" if "desktop" in self.request.source else "🌐 Claude Web"
        tk.Label(
            self, text=source_text,
            bg=BG_CARD, fg=FG_DIM,
            font=("Segoe UI", 9, "italic"),
            anchor="w",
        ).pack(fill=tk.X)

        # Fields
        self._field("Server", self.request.mcp_server or "—", FG_SERVER)
        self._field("Tool", self.request.tool_name or "—", FG_TOOL)

        # Parameters box
        if self.request.tool_params:
            params_text = json.dumps(self.request.tool_params, indent=2)
            if len(params_text) > 600:
                params_text = params_text[:600] + "\n… (truncated)"
            self._params_box(params_text)

        # Buttons
        btn_frame = tk.Frame(self, bg=BG_CARD, pady=8)
        btn_frame.pack(fill=tk.X)

        tk.Button(
            btn_frame,
            text="✅  Allow",
            bg=BTN_ALLOW, fg=BTN_FG,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
            command=lambda: self.on_decide(self.request.request_id, allow=True),
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_frame,
            text="🚫  Block",
            bg=BTN_BLOCK, fg=BTN_FG,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
            command=lambda: self.on_decide(self.request.request_id, allow=False),
        ).pack(side=tk.LEFT)

        # Request ID (small, for debugging)
        tk.Label(
            self,
            text=f"ID: {self.request.request_id[:8]}…",
            bg=BG_CARD, fg=FG_DIM,
            font=("Segoe UI", 7),
            anchor="e",
        ).pack(fill=tk.X, pady=(4, 0))

    def _field(self, label: str, value: str, value_colour: str = FG_MAIN) -> None:
        row = tk.Frame(self, bg=BG_CARD)
        row.pack(fill=tk.X, pady=1)
        tk.Label(
            row, text=f"{label}:", width=8, anchor="w",
            bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)
        tk.Label(
            row, text=value, anchor="w",
            bg=BG_CARD, fg=value_colour, font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _params_box(self, text: str) -> None:
        tk.Label(
            self, text="Parameters:", anchor="w",
            bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(4, 0))
        box = tk.Text(
            self, height=min(8, text.count("\n") + 1),
            bg="#12121c", fg=FG_MAIN,
            font=("Consolas", 8), relief=tk.FLAT,
            wrap=tk.WORD, padx=4, pady=4,
            state=tk.NORMAL,
        )
        box.insert("1.0", text)
        box.config(state=tk.DISABLED)
        box.pack(fill=tk.X, pady=(0, 4))


# ---------------------------------------------------------------------------
# Main popup window
# ---------------------------------------------------------------------------

class MCPAuthPopup:
    """
    Main application window managing the queue of authorization requests.

    Call ``start()`` on the main thread — it enters the Tkinter event loop.
    """

    def __init__(self) -> None:
        self.root = tk.Tk()
        self._setup_window()
        self._active_cards: Dict[str, RequestCard] = {}
        self._known_ids: set = set()
        self._poll_pending()   # kick off polling

    def _setup_window(self) -> None:
        root = self.root
        root.title("MCP Authorization Monitor")
        root.configure(bg=BG_DARK)
        root.attributes("-topmost", settings.popup_always_on_top)
        root.minsize(480, 100)
        root.maxsize(680, 900)

        # Position in top-right corner
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        root.geometry(f"500x650+{sw - 520}+30")

        # Title bar
        bar = tk.Frame(root, bg=ACCENT, padx=12, pady=8)
        bar.pack(fill=tk.X)
        tk.Label(
            bar,
            text="🛡  MCP Authorization Monitor",
            bg=ACCENT, fg=BG_DARK,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)

        self._status_lbl = tk.Label(
            bar, text="● Watching…",
            bg=ACCENT, fg=BG_DARK,
            font=("Segoe UI", 9),
        )
        self._status_lbl.pack(side=tk.RIGHT)

        # Scrollable container for request cards
        outer = tk.Frame(root, bg=BG_DARK)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        canvas = tk.Canvas(outer, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._cards_frame = tk.Frame(canvas, bg=BG_DARK)

        self._cards_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._cards_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas = canvas

        # Empty state label
        self._empty_lbl = tk.Label(
            self._cards_frame,
            text="No pending requests\n\nAll MCP requests will appear here\nfor your approval.",
            bg=BG_DARK, fg=FG_DIM,
            font=("Segoe UI", 10),
            justify=tk.CENTER, pady=40,
        )
        self._empty_lbl.pack(expand=True)

        # Footer
        footer = tk.Frame(root, bg=BG_DARK, pady=4)
        footer.pack(fill=tk.X, padx=8)
        tk.Label(
            footer,
            text="Requests will time out after "
                 f"{int(settings.decision_timeout_seconds)}s if no decision is made",
            bg=BG_DARK, fg=FG_DIM, font=("Segoe UI", 8),
        ).pack()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_pending(self) -> None:
        """Fetch pending requests from the auth server and refresh cards."""
        try:
            resp = httpx.get(
                f"{settings.auth_server_url}/pending",
                timeout=2.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                requests = [MCPRequest(**r) for r in data.get("requests", [])]
                self.root.after(0, self._refresh_cards, requests)
                self._status_lbl.config(text="● Connected")
            else:
                self._status_lbl.config(text="⚠ Server error")
        except httpx.ConnectError:
            self._status_lbl.config(text="⚡ Connecting…")
        except Exception as exc:
            logger.debug("Poll error: %s", exc)
            self._status_lbl.config(text="⚠ Error")

        # Schedule next poll
        self.root.after(settings.popup_poll_interval_ms, self._poll_pending)

    # ------------------------------------------------------------------
    # Card management
    # ------------------------------------------------------------------

    def _refresh_cards(self, requests: List[MCPRequest]) -> None:
        current_ids = {r.request_id for r in requests}

        # Add new cards
        for req in requests:
            if req.request_id not in self._active_cards:
                self._add_card(req)

        # Remove decided/gone cards
        stale = [rid for rid in list(self._active_cards) if rid not in current_ids]
        for rid in stale:
            self._remove_card(rid)

        # Show/hide empty state
        if self._active_cards:
            self._empty_lbl.pack_forget()
            # Flash the window to grab attention
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self._empty_lbl.pack(expand=True)

    def _add_card(self, request: MCPRequest) -> None:
        card = RequestCard(self._cards_frame, request, self._on_decide)
        card.pack(fill=tk.X, pady=5, padx=4)
        self._active_cards[request.request_id] = card
        logger.debug("Card added for request %s", request.request_id)

    def _remove_card(self, request_id: str) -> None:
        card = self._active_cards.pop(request_id, None)
        if card:
            card.destroy()
        logger.debug("Card removed for request %s", request_id)

    # ------------------------------------------------------------------
    # Decision callback
    # ------------------------------------------------------------------

    def _on_decide(self, request_id: str, allow: bool) -> None:
        """Called when user clicks Allow or Block."""
        decision = AuthorizationDecision(request_id=request_id, allow=allow)
        try:
            resp = httpx.post(
                f"{settings.auth_server_url}/decide",
                content=decision.model_dump_json(),
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                action = "ALLOWED" if allow else "BLOCKED"
                logger.info("User decision: %s → %s", request_id[:8], action)
                self._remove_card(request_id)
            else:
                logger.warning("Decision rejected: %s", resp.text)
        except Exception as exc:
            logger.error("Failed to submit decision: %s", exc)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        logger.info("Popup window closed by user")
        self.root.withdraw()   # hide but keep polling

    def start(self) -> None:
        """Enter the Tkinter main loop (blocking)."""
        logger.info("MCP Auth popup starting")
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def run_popup() -> None:
    popup = MCPAuthPopup()
    popup.start()


if __name__ == "__main__":
    run_popup()
