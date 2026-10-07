import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


class SignalConnectionRecoveryTests(unittest.TestCase):
    def make_runner(self, api_connect, browser_connect):
        runner = object.__new__(HybridRunner)
        runner.cfg = SimpleNamespace(
            requires_broker=True, connect_timeout_sec=1.0, skip_api_connect=False,
            effective_mode="signals",
        )
        runner.api = SimpleNamespace(
            connect=AsyncMock(side_effect=api_connect), disconnect=AsyncMock(), get_balance=AsyncMock()
        )
        runner.browser = SimpleNamespace(connect=AsyncMock(side_effect=browser_connect), disconnect=AsyncMock())
        runner._api_connected = False
        runner._browser_connected = False
        runner._api_candles_disabled = False
        runner._api_candles_fail_count = 0
        runner._market_connect_failures = 0
        runner._next_market_connect_at = 0.0
        runner.logger = SimpleNamespace(log=Mock())
        runner.control = SimpleNamespace(update_risk=Mock())
        return runner

    def test_total_signal_feed_failure_is_not_reported_as_success_and_is_redacted(self):
        secret = "token-do-not-log-0123456789abcdef"
        runner = self.make_runner(RuntimeError(secret), RuntimeError(secret))
        connected = asyncio.run(runner._safe_connect())

        self.assertFalse(connected)
        events = [call.args[0] for call in runner.logger.log.call_args_list]
        self.assertIn("market_data_unavailable", events)
        self.assertNotIn("connect_complete", events)
        self.assertNotIn(secret, repr(runner.logger.log.call_args_list))
        self.assertGreater(runner._next_market_connect_at, 0)
        runner.api.disconnect.assert_awaited_once()
        runner.browser.disconnect.assert_awaited_once()

    def test_disconnected_api_is_retried_even_when_browser_fallback_is_up(self):
        runner = self.make_runner([RuntimeError("temporary failure"), None], None)
        runner._browser_connected = True

        self.assertTrue(asyncio.run(runner._safe_connect()))
        self.assertFalse(runner._api_connected)
        self.assertGreater(runner._next_market_connect_at, 0)

        runner._next_market_connect_at = 0.0
        self.assertTrue(asyncio.run(runner._safe_connect()))
        self.assertTrue(runner._api_connected)
        self.assertEqual(runner._next_market_connect_at, 0.0)
        self.assertEqual(runner.api.connect.await_count, 2)
        runner.api.get_balance.assert_not_awaited()

    def test_socket_reconnect_does_not_reset_candle_failure_backoff(self):
        runner = self.make_runner(None, None)
        runner._api_candles_fail_count = 297
        runner._next_market_connect_at = 12345.0
        self.assertTrue(asyncio.run(runner._safe_connect()))
        self.assertEqual(runner._next_market_connect_at, 12345.0)
        self.assertEqual(runner._api_candles_fail_count, 297)
        complete = [call for call in runner.logger.log.call_args_list if call.args[0] == "connect_complete"][0]
        self.assertFalse(complete.kwargs["candles_verified"])
        self.assertEqual(complete.kwargs["connected_adapters"], ["api", "browser"])

    def test_data_timeout_is_not_misreported_as_connect_failure(self):
        code, _ = HybridRunner._safe_adapter_error("browser", asyncio.TimeoutError(), phase="data")
        self.assertEqual(code, "data_timeout")

    def test_live_api_failure_still_blocks_without_browser_order_fallback(self):
        runner = self.make_runner(RuntimeError("temporary failure"), None)
        runner.cfg.effective_mode = "live"

        with self.assertRaisesRegex(RuntimeError, "Live trading blocked"):
            asyncio.run(runner._safe_connect())
        runner.browser.connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
