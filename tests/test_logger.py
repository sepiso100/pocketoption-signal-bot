import unittest
from unittest.mock import patch

from pocket_signal_bot.logger import JsonEventLogger


class JsonEventLoggerTests(unittest.TestCase):
    def capture(self, event_type, **payload):
        with patch("builtins.print") as output:
            JsonEventLogger().log(event_type, **payload)
        return output.call_args.args[0]

    def test_startup_shows_deployed_source_sha(self):
        line = self.capture(
            "startup",
            source_sha="ec8c34d1234567890abcdef",
            effective_mode="signals",
            api_is_demo=True,
            requires_broker=True,
        )
        self.assertIn("source_sha=ec8c34d1234567890abcdef", line)

    def test_startup_rejects_untrusted_source_sha(self):
        line = self.capture("startup", source_sha="token-must-not-be-logged")
        self.assertIn("source_sha=unknown", line)
        self.assertNotIn("token-must-not-be-logged", line)

    def test_data_error_shows_safe_code_and_hint_not_raw_error(self):
        line = self.capture(
            "data_error",
            adapter="browser",
            error_code="browser_quote_unavailable",
            hint="Enable PO_USE_WS_QUOTES or configure PO_PRICE_SELECTOR.",
            error="secret-bearing exception text",
        )
        self.assertIn("browser_quote_unavailable", line)
        self.assertIn("configure PO_PRICE_SELECTOR", line)
        self.assertNotIn("secret-bearing exception text", line)


if __name__ == "__main__":
    unittest.main()
