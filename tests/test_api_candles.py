"""Offline regressions against the pinned SDK's actual event/request models."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pocket_option.models import Asset, LoadHistoryPeriodFastResponse
from pocket_signal_bot.adapters.api_adapter import PocketApiConfig, PocketOptionApiAdapter


def response(index=1, asset=Asset.EURUSD, period=60, invalid=False):
    now = int(time.time()) // 60 * 60
    return LoadHistoryPeriodFastResponse.model_validate({
        'asset': asset, 'index': index, 'period': period,
        'data': [{'symbol_id': 1, 'time': now, 'open': '1.1',
                  'close': '1.2', 'high': '0.5' if invalid else '1.3',
                  'low': '1.0', 'volume': 40}],
    })


class ApiCandleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = PocketOptionApiAdapter(PocketApiConfig('private-session', '123'))
        self.emitter = SimpleNamespace(subscribe_to_asset=AsyncMock(), change_asset=AsyncMock())
        async def history(request):
            self.request = request
            await self.adapter._on_history(response(index=request.index))
        self.emitter.load_history_period = AsyncMock(side_effect=history)
        self.adapter._client = SimpleNamespace(emit=self.emitter)

    async def test_fetch_uses_real_sdk_emit_contract(self):
        rows = await self.adapter.get_candles('EURUSD', 60, 300)
        self.assertEqual(self.request.asset, Asset.EURUSD)
        self.assertEqual(self.request.period, 60)
        self.assertEqual(self.request.offset, 18000)
        self.assertEqual(rows[0]['close'], 1.2)
        self.assertEqual(rows[0]['volume'], 40.0)
        self.assertIsNone(self.adapter._history_request)
        self.emitter.subscribe_to_asset.assert_awaited_once_with(Asset.EURUSD)

    async def test_ignores_wrong_asset_period_and_request(self):
        self.adapter._history_request = (Asset.EURUSD, 60, 9)
        for payload in (response(index=8), response(index=9, asset=Asset.GBPUSD), response(index=9, period=300)):
            await self.adapter._on_history(payload)
            self.assertFalse(self.adapter._history_event.is_set())
        await self.adapter._on_history(response(index=9))
        self.assertTrue(self.adapter._history_event.is_set())

    async def test_rejects_invalid_ohlc(self):
        async def history(request):
            await self.adapter._on_history(response(index=request.index, invalid=True))
        self.emitter.load_history_period.side_effect = history
        with self.assertRaisesRegex(RuntimeError, 'no valid OHLC'):
            await self.adapter.get_candles('EURUSD', 60, 300)

    async def test_timeout_does_not_reuse_cached_rows_or_leak_credentials(self):
        self.emitter.load_history_period.side_effect = None
        self.adapter._history_event.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        with self.assertRaisesRegex(RuntimeError, 'history response timed out') as error:
            await self.adapter.get_candles('EURUSD', 60, 300)
        self.assertNotIn('private-session', str(error.exception))
        self.assertIsNone(self.adapter._history_request)

    async def test_invalid_asset_fails_before_emitting(self):
        with self.assertRaisesRegex(RuntimeError, 'unsupported'):
            await self.adapter.get_candles('NOT_A_BROKER_ASSET', 60, 300)
        self.emitter.load_history_period.assert_not_awaited()

    async def test_connect_waits_for_authentication(self):
        client = SimpleNamespace(on=SimpleNamespace(load_history_period_fast=lambda cb: None),
                                 emit=SimpleNamespace(auth=AsyncMock()),
                                 connect=AsyncMock(), wait_for_authorization=AsyncMock())
        with patch('pocket_option.PocketOptionClient', return_value=client), \
             patch('pocket_option.contrib.deals.MemoryDealsStorage', return_value=object()):
            await self.adapter.connect()
        client.wait_for_authorization.assert_awaited_once_with(timeout=15.0)
        auth = client.emit.auth.await_args.args[0]
        self.assertEqual(auth.uid, 123)
        auth_wire = auth.model_dump(mode='json', by_alias=True)
        self.assertEqual(auth_wire['sessionToken'], 'private-session')
        self.assertNotIn('session', auth_wire)

    async def test_invalid_region_is_not_silently_replaced_with_demo(self):
        self.adapter.cfg.region = 'INVALID_REGION'
        with self.assertRaisesRegex(RuntimeError, 'Unknown PO_REGION'):
            await self.adapter.connect()

    async def test_auth_failure_is_not_successful_connection(self):
        client = SimpleNamespace(on=SimpleNamespace(load_history_period_fast=lambda cb: None),
                                 emit=SimpleNamespace(auth=AsyncMock()), connect=AsyncMock(),
                                 wait_for_authorization=AsyncMock(side_effect=RuntimeError('auth failed')))
        self.adapter._deals = None
        with patch('pocket_option.PocketOptionClient', return_value=client):
            with self.assertRaisesRegex(RuntimeError, 'auth failed'):
                await self.adapter.connect()
        self.assertIsNone(self.adapter._deals)
