from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ControlState:
    """Process-local safety gate; remote commands never change trading mode."""

    def __init__(self, token: str | None = None, mode: str = "paper"):
        self.token = token or os.getenv("SIGNAL_CONTROL_TOKEN", "").strip()
        self.mode = mode
        self._paused = False
        self._stopped = False
        self._risk_halted_reason = ""
        self._pending_order = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return len(self.token) >= 32

    def is_order_allowed(self) -> bool:
        with self._lock:
            return not self._paused and not self._stopped

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "controls_enabled": self.enabled,
                "mode": self.mode,
                "paused": self._paused,
                "stopped": self._stopped,
                "risk_halted_reason": self._risk_halted_reason,
                "pending_order": self._pending_order,
                "start_allowed": self.mode == "signals" and not self._pending_order and not self._risk_halted_reason,
            }

    def update_risk(self, halted_reason: str, pending_order: bool) -> None:
        with self._lock:
            self._risk_halted_reason = str(halted_reason or "")[:64]
            self._pending_order = bool(pending_order)

    def command(self, action: str) -> bool:
        if action == "start":
            with self._lock:
                # Restart read-only signal publication, never trading or risk holds.
                if self.mode != "signals" or self._pending_order or self._risk_halted_reason:
                    return False
                self._stopped = False
                self._paused = False
        elif action == "pause":
            with self._lock:
                self._paused = True
        elif action == "resume":
            with self._lock:
                if not self._stopped:
                    self._paused = False
        elif action == "stop":
            with self._lock:
                self._stopped = True
                self._paused = True
        else:
            return False
        return True


class _Handler(BaseHTTPRequestHandler):
    server: "ControlHTTPServer"

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.state.token
        return bool(self.server.state.enabled) and hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        if self.path == "/v1/control/status":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            self._send(200, self.server.state.status())
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/control":
            self._send(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length))
            action = payload.get("action") if isinstance(payload, dict) else None
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "invalid_json"})
            return
        if not isinstance(action, str) or action not in {"start", "pause", "resume", "stop"}:
            self._send(400, {"error": "action_must_be_start_pause_resume_or_stop"})
            return
        if not self.server.state.command(action):
            self._send(409, {"error": "start_blocked_by_safety", **self.server.state.status()})
            return
        self._send(200, self.server.state.status())


class ControlHTTPServer(ThreadingHTTPServer):
    def __init__(self, state: ControlState, host: str | None = None, port: int | None = None):
        self.state = state
        bind_host = host if host is not None else os.getenv("SIGNAL_CONTROL_HOST", "0.0.0.0")
        bind_port = port if port is not None else int(os.getenv("PORT", "8080"))
        super().__init__((bind_host, bind_port), _Handler)
        self.daemon_threads = True


class ControlServer:
    def __init__(self, state: ControlState):
        self.state = state
        self.httpd: ControlHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.httpd = ControlHTTPServer(self.state)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="control-api", daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
