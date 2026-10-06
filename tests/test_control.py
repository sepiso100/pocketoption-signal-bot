import json
import threading
import unittest
from http.client import HTTPConnection

from pocket_signal_bot.control import ControlHTTPServer, ControlState


class ControlApiTests(unittest.TestCase):
    def setUp(self):
        self.state = ControlState(token="secret", mode="live")
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
        status, payload = self.request("POST", "/v1/control", {"action": "pause"}, "secret")
        self.assertEqual(status, 200)
        self.assertTrue(payload["paused"])
        self.assertEqual(payload["mode"], "live")
        self.assertFalse(self.state.is_order_allowed())
        status, payload = self.request("POST", "/v1/control", {"action": "resume"}, "secret")
        self.assertEqual(status, 200)
        self.assertTrue(self.state.is_order_allowed())
        self.assertEqual(payload["mode"], "live")

    def test_stop_is_terminal_for_order_gate(self):
        status, _ = self.request("POST", "/v1/control", {"action": "stop"}, "secret")
        self.assertEqual(status, 200)
        self.assertFalse(self.state.is_order_allowed())
        self.request("POST", "/v1/control", {"action": "resume"}, "secret")
        self.assertFalse(self.state.is_order_allowed())

    def test_only_supported_actions(self):
        status, _ = self.request("POST", "/v1/control", {"action": "live"}, "secret")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
