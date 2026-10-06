import asyncio
import dataclasses
import datetime
import enum
import sys
import types
import unittest
from unittest.mock import AsyncMock

from pocket_signal_bot.adapters.api_adapter import PocketApiConfig, PocketOptionApiAdapter


class FakeAsset(enum.StrEnum):
    EURUSD_otc = "EURUSD_otc"


@dataclasses.dataclass
class FakeHistoryRequest:
    asset: FakeAsset
    index: int | None
    time: float
    offset: int
    period: int


@dataclasses.dataclass
class FakeChangeAssetRequest:
    asset: FakeAsset
    period: int


@dataclasses.dataclass
class FakeCandle:
    timestamp: datetime.datetime
    open: float
    high: float
    low: float
    close: float


class FakeCandleStorage:
    def __init__(self):
        self.rows = []
        self.calls = []

    async def get_candles(self, asset, *, timeframe, count):
        self.calls.append((asset, timeframe, count))
        return self.rows[-count:]


class ApiAdapterSdk04Tests(unittest.TestCase):
    def install_sdk_mocks(self):
        state = types.SimpleNamespace(client=None, init_args=None, region=None)
        models = types.ModuleType("pocket_option.models")

        class AuthorizationData:
            @classmethod
            def model_validate(cls, payload):
                return dict(payload)

        models.Asset = FakeAsset
        models.AuthorizationData = AuthorizationData
        models.LoadHistoryPeriodRequest = FakeHistoryRequest
        models.ChangeAssetRequest = FakeChangeAssetRequest

        package = types.ModuleType("pocket_option")
        package.__path__ = []

        class FakeClient:
            def __init__(self):
                self.storage = FakeCandleStorage()
                self.candles = self.storage
                self.deals = object()
                self.authenticated = False
                self.authorization = None
                self.emit = types.SimpleNamespace()
                self.emit.auth = AsyncMock(side_effect=self.mark_authenticated)
                self.emit.subscribe_to_asset = AsyncMock()
                self.emit.change_asset = AsyncMock()
                self.emit.load_history_period = AsyncMock(side_effect=self.load_history)
                state.client = self

            async def mark_authenticated(self, authorization):
                self.authenticated = True

            async def load_history(self, request):
                self.last_history_request = request
                now = datetime.datetime.now(datetime.timezone.utc)
                self.storage.rows = [
                    FakeCandle(now - datetime.timedelta(seconds=120), 1.1, 1.3, 1.0, 1.2),
                    FakeCandle(now - datetime.timedelta(seconds=60), 1.2, 1.4, 1.1, 1.3),
                ]

            async def connect(self, region):
                state.region = region
                await self.emit.auth(self.authorization)

            async def wait_for_authorization(self, timeout):
                if not self.authenticated:
                    raise TimeoutError

            async def disconnect(self):
                pass

        package.PocketOptionClient = FakeClient
        constants = types.ModuleType("pocket_option.constants")
        constants.Regions = types.SimpleNamespace(DEMO="demo-endpoint")
        contrib = types.ModuleType("pocket_option.contrib")
        contrib.__path__ = []
        contrib_init = types.ModuleType("pocket_option.contrib.default_init")

        def default_init(client, *, authorization, sub_assets=None, sub_period=30):
            client.authorization = authorization
            state.init_args = (sub_assets, sub_period)

        contrib_init.default_init = default_init
        modules = {
            "pocket_option": package,
            "pocket_option.models": models,
            "pocket_option.constants": constants,
            "pocket_option.contrib": contrib,
            "pocket_option.contrib.default_init": contrib_init,
        }
        self._modules_patch = unittest.mock.patch.dict(sys.modules, modules)
        self._modules_patch.start()
        self.addCleanup(self._modules_patch.stop)
        return state

    def test_sdk_04_auth_subscription_history_and_real_candle_normalization(self):
        state = self.install_sdk_mocks()
        adapter = PocketOptionApiAdapter(
            PocketApiConfig("private-session", "12345", symbol="EURUSD_otc", timeframe_sec=60)
        )

        async def exercise():
            await adapter.connect()
            result = await adapter.get_candles("EURUSD_otc", 60, 2)
            return result

        result = asyncio.run(exercise())
        client = state.client
        self.assertEqual(state.region, "demo-endpoint")
        self.assertEqual(state.init_args, ([FakeAsset.EURUSD_otc], 60))
        client.emit.auth.assert_awaited_once()
        client.emit.load_history_period.assert_awaited_once()
        request = client.last_history_request
        self.assertEqual(request.asset, FakeAsset.EURUSD_otc)
        self.assertEqual(request.index, None)
        self.assertEqual(request.offset, 2)
        self.assertEqual(request.period, 60)
        client.emit.subscribe_to_asset.assert_not_awaited()
        client.emit.change_asset.assert_not_awaited()
        self.assertEqual(len(result), 2)
        self.assertEqual([row["close"] for row in result], [1.2, 1.3])
        self.assertTrue(all(isinstance(row["time"], float) for row in result))
        asyncio.run(adapter.disconnect())

    def test_empty_verified_storage_raises_safe_pending_error_without_fabricating_data(self):
        state = self.install_sdk_mocks()
        adapter = PocketOptionApiAdapter(PocketApiConfig("not-logged", "12345"))

        async def exercise():
            await adapter.connect()
            adapter._history_requested_at[("EURUSD_otc", 60)] = __import__("time").monotonic()
            return await adapter.get_candles("EURUSD_otc", 60, 10)

        with self.assertRaisesRegex(RuntimeError, "No verified candle data"):
            asyncio.run(exercise())
        self.assertEqual(state.client.storage.rows, [])
        asyncio.run(adapter.disconnect())


if __name__ == "__main__":
    unittest.main()
