import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from pocket_signal_bot.analysis import analyze_market
from pocket_signal_bot.signal_publisher import SignalPublisher


class AnalysisTests(unittest.TestCase):
    def upward_candles(self, *, with_ohlc=True):
        candles = []
        price = 100.0
        for index in range(70):
            open_price = price
            price += 0.15
            row = {"time": 1_800_000_000 + index * 60, "close": price}
            if with_ohlc:
                row.update({"open": open_price, "high": price + 0.03, "low": open_price - 0.02})
            candles.append(row)
        if with_ohlc:
            # Close beyond prior swing high gives a transparent BOS signal.
            candles[-1].update({"open": price - 0.8, "high": price + 0.2, "low": price - 0.9, "close": price + 0.15})
        return candles

    def test_ohlc_analysis_reports_break_and_pressure_proxy(self):
        result = analyze_market(self.upward_candles(), "CALL")
        self.assertEqual(result["signal"], "CALL")
        self.assertIn("Bullish", result["market_bias"])
        self.assertIn("break of structure", result["structure"])
        self.assertIn("proxy", result["candle_pressure"])
        self.assertGreaterEqual(result["alignment_score"], 70)
        self.assertIn("not win probability", result["score_note"])

    def test_closes_only_never_invents_structure_or_order_flow(self):
        result = analyze_market(self.upward_candles(with_ohlc=False), "CALL")
        self.assertEqual(result["signal"], "CALL")
        self.assertTrue(result["structure"].startswith("Unavailable"))
        self.assertTrue(result["candle_pressure"].startswith("Unavailable"))
        self.assertIn("not supplied", result["flow_note"])

    def test_countertrend_candidate_is_rejected(self):
        result = analyze_market(self.upward_candles(), "PUT")
        self.assertEqual(result["signal"], "NO_TRADE")

    def test_too_little_data_produces_no_trade(self):
        result = analyze_market([{"close": 1.0}, {"close": 1.1}], "CALL")
        self.assertEqual(result["signal"], "NO_TRADE")
        self.assertEqual(result["alignment_score"], 0)

    def test_signal_publisher_requires_https_and_long_token(self):
        self.assertFalse(SignalPublisher("http://example.com/signals", "x" * 40).configured)
        self.assertFalse(SignalPublisher("https://example.com/signals", "short").configured)
        self.assertTrue(SignalPublisher("https://example.com/signals", "x" * 40).configured)

    def test_signal_publisher_retries_partial_delivery_response(self):
        opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps({"ok": True, "sent": 1, "failed": 0, "skipped": 0}).encode()
        opener.open.side_effect = [
            urllib.error.HTTPError("https://example.com/signals", 503, "partial", {}, None),
            response,
        ]
        publisher = SignalPublisher("https://example.com/signals", "x" * 40)
        with patch("pocket_signal_bot.signal_publisher.urllib.request.build_opener", return_value=opener), \
             patch("pocket_signal_bot.signal_publisher.time.sleep"):
            result = publisher._post({"signal_id": "test"})
        self.assertTrue(result["ok"])
        self.assertEqual(opener.open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
