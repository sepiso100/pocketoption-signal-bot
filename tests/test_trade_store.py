import tempfile
import unittest
from pathlib import Path

from pocket_signal_bot.trade_store import TradeStore


class TradeStoreTests(unittest.TestCase):
    def test_restart_archives_previous_session_instead_of_dropping_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "trades.json")
            first = TradeStore(path)
            first.begin_session({"mode": "demo"})
            first.record_trade({"pnl": -1.0, "ts": "2026-10-06T00:00:00+00:00"})
            second = TradeStore(path)
            second.begin_session({"mode": "live"})
            sessions = second.all_sessions()
            self.assertEqual(len(sessions), 2)
            self.assertTrue(sessions[0]["interrupted"])
            self.assertEqual(len(sessions[0]["trades"]), 1)
            self.assertEqual(sessions[0]["summary"]["total_pnl"], -1.0)


if __name__ == "__main__":
    unittest.main()
