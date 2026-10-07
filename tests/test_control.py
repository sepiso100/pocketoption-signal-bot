import json
import threading
import unittest
from http.client import HTTPConnection

from pocket_signal_bot.control import ControlHTTPServer, ControlState


class ControlApiTests(unittest.TestCase):
    def setUp(self):
        self.token = "test-control-token-0123456789abcdef"
        self.state = ControlState(token=self.token, mode="live")
        self.server = ControlHTTPServer(self.state, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, token=None):
        conn = HTTPConnection("127.0.0.1", self.port)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        encoded = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, encoded, headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_health_is_public_and_generic(self):
        status, payload = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_controls_require_bearer_and_never_change_mode(self):
        status, _ = self.request("GET", "/v1/control/status")
        self.assertEqual(status, 401)
        status, payload = self.request("POST", "/v1/control", {"action": "pause"}, self.token)
        self.assertEqual(status, 200)
        self.assertTrue(payload["paused"])
        self.assertEqual(payload["mode"], "live")
        self.assertFalse(self.state.is_order_allowed())
        status, payload = self.request("POST", "/v1/control", {"action": "resume"}, self.token)
        self.assertEqual(status, 200)
        self.assertTrue(self.state.is_order_allowed())
        self.assertEqual(payload["mode"], "live")

    def test_stop_is_terminal_for_order_gate(self):
        status, _ = self.request("POST", "/v1/control", {"action": "stop"}, self.token)
        self.assertEqual(status, 200)
        self.assertFalse(self.state.is_order_allowed())
        self.request("POST", "/v1/control", {"action": "resume"}, self.token)
        self.assertFalse(self.state.is_order_allowed())

    def test_status_exposes_risk_hold_without_secrets(self):
        self.state.update_risk("settlement_unverified", True)
        status, payload = self.request("GET", "/v1/control/status", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(payload["risk_halted_reason"], "settlement_unverified")
        self.assertTrue(payload["pending_order"])
        self.assertNotIn(self.token, str(payload))

    def test_start_restarts_stopped_read_only_signal_worker(self):
        self.state.mode = "signals"
        self.request("POST", "/v1/control", {"action": "stop"}, self.token)
        status, payload = self.request("POST", "/v1/control", {"action": "start"}, self.token)
        self.assertEqual(status, 200)
        self.assertFalse(payload["stopped"])
        self.assertFalse(payload["paused"])
        self.assertEqual(payload["mode"], "signals")
        self.assertTrue(self.state.is_order_allowed())

    def test_start_never_restarts_trading_modes(self):
        for mode in ("live", "demo", "paper"):
            self.state.mode = mode
            self.state.command("stop")
            status, payload = self.request("POST", "/v1/control", {"action": "start"}, self.token)
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"], "start_blocked_by_safety")
            self.assertTrue(payload["stopped"])
            self.assertFalse(payload["start_allowed"])

    def test_start_preserves_risk_holds_and_pending_orders(self):
        self.state.mode = "signals"
        for reason, pending in (("settlement_unverified", False), ("", True)):
            self.state.command("stop")
            self.state.update_risk(reason, pending)
            status, payload = self.request("POST", "/v1/control", {"action": "start"}, self.token)
            self.assertEqual(status, 409)
            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["risk_halted_reason"], reason)
            self.assertEqual(payload["pending_order"], pending)

    def test_start_requires_authorization(self):
        self.state.mode = "signals"
        self.state.command("stop")
        status, _ = self.request("POST", "/v1/control", {"action": "start"})
        self.assertEqual(status, 401)
        self.assertTrue(self.state.status()["stopped"])

    def test_only_supported_actions(self):
        status, _ = self.request("POST", "/v1/control", {"action": "live"}, self.token)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
