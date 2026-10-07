import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pocket_signal_bot.adapters.browser_adapter import BrowserConfig, PocketOptionBrowserAdapter


class BrowserQuoteSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_quote_is_not_returned_as_current(self):
        adapter = PocketOptionBrowserAdapter(BrowserConfig())
        adapter._page = SimpleNamespace(locator=lambda selector: SimpleNamespace(first=SimpleNamespace(text_content=AsyncMock(return_value=None))))
        adapter._last_ws_price = 1.25
        adapter._last_ws_ts = time.time() - 60
        with self.assertRaisesRegex(RuntimeError, 'Could not read price'):
            await adapter._read_price()

    async def test_fresh_quote_can_be_read(self):
        adapter = PocketOptionBrowserAdapter(BrowserConfig())
        adapter._page = object()
        adapter._last_ws_price = 1.25
        adapter._last_ws_ts = time.time()
        self.assertEqual(await adapter._read_price(), 1.25)

    async def test_otc_and_regular_feeds_are_not_interchangeable(self):
        adapter = PocketOptionBrowserAdapter(BrowserConfig(quote_asset='EURUSD_otc'))
        self.assertIsNone(adapter._walk_quote_json(['EURUSD', 1700000000, 1.25], 'EURUSD_otc'))
        self.assertEqual(adapter._walk_quote_json(['EURUSD_otc', 1700000000, 1.25], 'EURUSD_otc'), 1.25)
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            await adapter.get_candles('EURUSD', 60, 50)

    async def test_outgoing_requests_never_become_market_quotes(self):
        adapter = PocketOptionBrowserAdapter(BrowserConfig(quote_asset='EURUSD'))
        adapter._ingest_ws_payload('42["updateStream", [["EURUSD", 1700000000, 1.25]]]', direction='send')
        self.assertIsNone(adapter._last_ws_price)
        adapter._ingest_ws_payload('42["updateStream", [["EURUSD", 1700000000, 1.25]]]', direction='recv')
        self.assertEqual(adapter._last_ws_price, 1.25)
