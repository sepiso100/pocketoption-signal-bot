import os
import unittest
from unittest.mock import patch

from pocket_signal_bot.config import BotConfig, validate_config


class ConfigSafetyTests(unittest.TestCase):
    def test_demo_mode_cannot_be_switched_to_real_by_stale_flag(self):
        cfg = BotConfig(mode="demo", po_is_demo=False, po_session="session", po_uid="123")
        self.assertTrue(cfg.api_is_demo)
        validate_config(cfg)

    def test_live_requires_explicit_confirmed_real_region_and_fixed_stake(self):
        cfg = BotConfig(
            mode="live", po_live_confirmed=True, po_session="session", po_uid="123",
            po_region="REAL", trade_amount=1.0,
        )
        with patch.dict(os.environ, {"PO_REGION": "REAL", "PO_TRADE_AMOUNT": "1"}):
            validate_config(cfg)
        self.assertFalse(cfg.api_is_demo)

    def test_live_rejects_demo_region(self):
        cfg = BotConfig(
            mode="live", po_live_confirmed=True, po_session="session", po_uid="123",
            po_region="DEMO", trade_amount=1.0,
        )
        with patch.dict(os.environ, {"PO_REGION": "DEMO", "PO_TRADE_AMOUNT": "1"}):
            with self.assertRaises(SystemExit):
                validate_config(cfg)

    def test_live_requires_confirmation(self):
        cfg = BotConfig(mode="live", po_live_confirmed=False, po_session="session", po_uid="123")
        with patch.dict(os.environ, {"PO_REGION": "REAL", "PO_TRADE_AMOUNT": "1"}):
            with self.assertRaises(SystemExit):
                validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
