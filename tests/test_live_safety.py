import asyncio
import unittest
from types import SimpleNamespace

from pocket_signal_bot.runner import HybridRunner


class LiveSafetyTests(unittest.TestCase):
    def test_unknown_settlement_is_not_counted_as_a_loss(self):
        with self.assertRaisesRegex(RuntimeError, "Settlement is unverified"):
            HybridRunner._normalize_settled_pnl({"result": "unknown", "won": False}, 1.0, 80.0)

    def test_unavailable_api_never_falls_back_to_browser_orders(self):
        runner = HybridRunner.__new__(HybridRunner)
        runner.cfg = SimpleNamespace(requires_broker=True)
        runner._api_connected = False
        with self.assertRaisesRegex(RuntimeError, "refusing browser order execution"):
            asyncio.run(runner._place_with_failover("CALL", 1.0))

    def test_timestamp_parser_accepts_seconds_milliseconds_and_iso(self):
        self.assertEqual(HybridRunner._timestamp_seconds(1_760_000_000), 1_760_000_000)
        self.assertEqual(HybridRunner._timestamp_seconds(1_760_000_000_000), 1_760_000_000)
        self.assertEqual(
            HybridRunner._timestamp_seconds("2025-10-09T08:53:20+00:00"),
            1_760_000_000,
        )
        self.assertIsNone(HybridRunner._timestamp_seconds("unknown"))


if __name__ == "__main__":
    unittest.main()
