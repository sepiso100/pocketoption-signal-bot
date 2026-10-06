import json
import tempfile
import unittest
from pathlib import Path

from pocket_signal_bot.risk import RiskConfig, RiskManager


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tempdir.name) / "risk.json")
        self.cfg = RiskConfig(70, 1500, 3, 20, 2.0)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_unknown_payout_and_balance_fail_closed(self):
        risk = RiskManager(self.cfg, 100.0, self.path)
        self.assertEqual(risk.can_trade(0.0, 0, 100.0), (False, "payout_unverified"))
        self.assertEqual(risk.can_trade(80.0, 0, 0.0), (False, "balance_unverified"))

    def test_counters_survive_restart(self):
        first = RiskManager(self.cfg, 100.0, self.path)
        first.set_verified_balance(100.0)
        first.register_result(False)
        resumed = RiskManager(self.cfg, 999.0, self.path)
        self.assertEqual(resumed.trades_today, 1)
        self.assertEqual(resumed.consecutive_losses, 1)
        self.assertEqual(resumed.day_start_balance, 100.0)

    def test_pending_order_survives_restart_and_blocks_trades(self):
        first = RiskManager(self.cfg, 100.0, self.path)
        first.set_verified_balance(100.0)
        first.mark_pending()
        resumed = RiskManager(self.cfg, 100.0, self.path)
        self.assertTrue(resumed.pending_order)
        self.assertEqual(resumed.can_trade(80.0, 0, 100.0), (False, "pending_trade_reconciliation"))

    def test_corrupt_state_fails_closed(self):
        Path(self.path).write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Risk state is unreadable"):
            RiskManager(self.cfg, 100.0, self.path)

    def test_repeated_order_on_same_candle_is_blocked_after_restart(self):
        first = RiskManager(self.cfg, 100.0, self.path)
        first.set_verified_balance(100.0)
        first.mark_pending("1760000000")
        first.register_result(True)
        resumed = RiskManager(self.cfg, 100.0, self.path)
        self.assertEqual(
            resumed.can_trade(80.0, 0, 100.0, candle_key="1760000000"),
            (False, "already_traded_this_candle"),
        )

    def test_fixed_stake_is_not_changed_by_results(self):
        risk = RiskManager(self.cfg, 100.0, self.path)
        risk.mark_pending()
        risk.register_result(False)
        self.assertEqual(risk.consecutive_losses, 1)
        self.assertFalse(risk.pending_order)


if __name__ == "__main__":
    unittest.main()
