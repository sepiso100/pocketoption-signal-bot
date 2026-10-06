import asyncio
import unittest
from types import SimpleNamespace

from pocket_signal_bot.config import BotConfig, validate_config
from pocket_signal_bot.runner import HybridRunner


class SignalModeTests(unittest.TestCase):
    def test_signal_mode_is_read_only_and_demo_authenticated(self):
        cfg = BotConfig(
            mode="signals",
            po_session="test-session",
            po_uid="12345",
            signal_ingest_url="https://passkeys.example/v1/signals",
            signal_ingest_token="s" * 40,
            skip_api_connect=False,
        )
        validate_config(cfg)
        self.assertTrue(cfg.requires_broker)
        self.assertTrue(cfg.api_is_demo)

    def test_signal_mode_fails_closed_without_private_delivery_settings(self):
        cfg = BotConfig(mode="signals", po_session="x", po_uid="12345", skip_api_connect=False)
        with self.assertRaises(SystemExit):
            validate_config(cfg)

    def test_signal_mode_cannot_call_broker_order_method(self):
        runner = object.__new__(HybridRunner)
        runner.cfg = SimpleNamespace(effective_mode="signals", requires_broker=True)
        with self.assertRaisesRegex(RuntimeError, "categorically blocks"):
            asyncio.run(runner._place_with_failover("CALL", 1.0))


if __name__ == "__main__":
    unittest.main()
