from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import asyncio
import time


class CandleDataPending(RuntimeError):
    """The API is connected, but no verified candle data is available yet."""


@dataclass
class PocketApiConfig:
    session: str
    uid: str
    is_demo: bool = True
    region: str = "DEMO"
    symbol: str = "EURUSD_otc"
    timeframe_sec: int = 60


class PocketOptionApiAdapter:
    """
    Unofficial API adapter.
    This wrapper is defensive: if SDK is not installed, it raises clear errors.
    """

    def __init__(self, cfg: PocketApiConfig):
        self.cfg = cfg
        self._client = None
        self._deals = None
        self._asset_type = None
        self._history_request_type = None
        self._subscriptions: set[tuple[str, int]] = set()
        self._history_requested_at: dict[tuple[str, int], float] = {}

    async def connect(self) -> None:
        try:
            from pocket_option import PocketOptionClient  # type: ignore
            from pocket_option.models import Asset, AuthorizationData, LoadHistoryPeriodRequest  # type: ignore
            from pocket_option.contrib.default_init import default_init  # type: ignore
        except Exception as e:
            raise RuntimeError("Install the compatible pocket-option 0.4.0 SDK.") from e

        client = PocketOptionClient()
        self._client = client
        self._asset_type = Asset
        self._history_request_type = LoadHistoryPeriodRequest
        auth = AuthorizationData.model_validate(
            {
                "session": self.cfg.session,
                "isDemo": 1 if self.cfg.is_demo else 0,
                "uid": int(self.cfg.uid),
                "platform": 2,
                "isFastHistory": True,
                "isOptimized": True,
            }
        )
        asset_value = Asset(self.cfg.symbol)
        default_init(
            client,
            authorization=auth,
            sub_assets=[asset_value],
            sub_period=self.cfg.timeframe_sec,
        )
        self._subscriptions.add((str(asset_value), int(self.cfg.timeframe_sec)))
        regions = __import__("pocket_option.constants", fromlist=["Regions"]).Regions
        await client.connect(getattr(regions, self.cfg.region, regions.DEMO))
        # SDK 0.4.0's default_init sends auth on connect and initializes candle,
        # asset, and deal storage. Do not treat a socket connection as auth success.
        await client.wait_for_authorization(timeout=30.0)
        self._deals = client.deals

    async def disconnect(self) -> None:
        # Clear references even when SDK partial-connection cleanup fails;
        # otherwise the runner can mistake a dead client for a reusable one.
        client = self._client
        self._client = None
        self._deals = None
        self._asset_type = None
        self._history_request_type = None
        self._subscriptions.clear()
        self._history_requested_at.clear()
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def get_candles(self, asset: str, timeframe_sec: int, count: int) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("API adapter is not connected")
        if self._asset_type is None or self._history_request_type is None:
            raise RuntimeError("PocketOption SDK initialization is incomplete")
        timeframe_sec = int(timeframe_sec)
        count = max(1, int(count))
        asset_value = self._asset_type(asset)
        key = (str(asset_value), timeframe_sec)

        if key not in self._subscriptions:
            try:
                from pocket_option.models import ChangeAssetRequest  # type: ignore
                await self._client.emit.subscribe_to_asset(asset_value)
                await self._client.emit.change_asset(
                    ChangeAssetRequest(asset=asset_value, period=timeframe_sec)
                )
            except Exception:
                raise RuntimeError("PocketOption market subscription failed; verify PO_SYMBOL and timeframe.") from None
            self._subscriptions.add(key)

        try:
            storage = self._client.candles
        except Exception:
            raise RuntimeError("PocketOption candle storage was not initialized by the SDK.") from None

        # SDK 0.4.0 stores each history bar as four price updates; multiply the
        # raw storage limit so the requested count means actual OHLC candles.
        storage_count = count * 4

        async def read_storage() -> list[Any]:
            try:
                rows = await storage.get_candles(
                    asset_value,
                    timeframe=timeframe_sec,
                    count=storage_count,
                )
                return list(rows or [])
            except Exception:
                raise RuntimeError("PocketOption candle storage read failed.") from None

        candles = await read_storage()
        request_key = key
        requested_at = self._history_requested_at.get(request_key, 0.0)
        now = time.monotonic()
        if len(candles) < count and now - requested_at >= 30.0:
            request = self._history_request_type(
                asset=asset_value,
                index=None,
                time=datetime.now(timezone.utc).timestamp(),
                offset=count,
                period=timeframe_sec,
            )
            try:
                await self._client.emit.load_history_period(request)
                self._history_requested_at[request_key] = now
            except Exception:
                raise RuntimeError("PocketOption historical-candle request failed.") from None

            # The SDK storage is populated asynchronously by its
            # loadHistoryPeriodFast listener. Wait briefly on the first request.
            deadline = time.monotonic() + 5.0
            while len(candles) < count and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                candles = await read_storage()

        normalized: list[dict[str, Any]] = []
        for row in candles[-count:]:
            def field(*names: str):
                for name in names:
                    value = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
                    if value is not None:
                        return value
                return None

            close = field("close", "c")
            ts = field("time", "timestamp", "t")
            if close is None:
                continue
            if hasattr(ts, "timestamp") and callable(ts.timestamp):
                ts = ts.timestamp()
            try:
                candle = {"close": float(close), "time": ts}
                for target, names in (
                    ("open", ("open", "o")),
                    ("high", ("high", "h")),
                    ("low", ("low", "l")),
                    ("volume", ("volume", "vol", "v")),
                ):
                    value = field(*names)
                    if value is not None:
                        candle[target] = float(value)
                normalized.append(candle)
            except (TypeError, ValueError, OverflowError):
                continue
        if not normalized:
            raise CandleDataPending("No verified candle data has arrived from PocketOption yet.")
        return normalized

    async def get_payout_pct(self, asset: str) -> float:
        # Never fabricate a payout: the runner must verify it elsewhere or block trading.
        raise RuntimeError("Unofficial API adapter cannot verify payout.")

    async def place_order(self, asset: str, amount: float, direction: str, expiry_sec: int) -> str:
        if self._client is None or self._deals is None:
            raise RuntimeError("API adapter is not connected")
        try:
            from pocket_option.models import Asset, DealAction  # type: ignore
        except Exception as e:
            raise RuntimeError("Install unofficial SDK: pip install pocket-option") from e

        action = DealAction.CALL if direction == "CALL" else DealAction.PUT
        asset_enum = getattr(Asset, asset, None)
        if asset_enum is None:
            raise RuntimeError(f"Asset '{asset}' not found in SDK Asset enum")
        deal = await self._deals.open_deal(
            asset=asset_enum,
            amount=amount,
            action=action,
            is_demo=1 if self.cfg.is_demo else 0,
            option_type=100,
            time=expiry_sec,
        )
        order_id = str(getattr(deal, "id", "")) or str(getattr(deal, "deal_id", ""))
        if not order_id:
            raise RuntimeError("Broker accepted no verifiable order identifier; reconcile before retrying.")
        return order_id

    async def check_result(self, order_id: str, wait_sec: int) -> dict[str, Any]:
        if self._deals is None:
            await asyncio.sleep(wait_sec + 1)
            return {"order_id": order_id, "result": "unknown", "won": False}
        try:
            deal = type("DealObj", (), {"id": order_id})
            res = await self._deals.check_deal_result(wait_time=wait_sec, deal=deal)
            payload = res if isinstance(res, dict) else res.__dict__
            result = str(payload.get("result", payload.get("status", "unknown"))).lower()
            profit = float(payload.get("profit_amount", 0))
            won = result == "win" or profit > 0
            return {"order_id": order_id, "result": result, "won": won, "pnl": profit, "raw": payload}
        except Exception:
            await asyncio.sleep(wait_sec + 1)
            return {"order_id": order_id, "result": "unknown", "won": False}

    async def get_balance(self) -> float:
        if self._client is None:
            return 0.0
        try:
            for name in ("balance", "get_balance"):
                method = getattr(self._client, name, None)
                if method is None:
                    continue
                maybe = method()  # type: ignore[misc]
                bal = await maybe if asyncio.iscoroutine(maybe) else maybe
                if isinstance(bal, (int, float)):
                    return float(bal)
                if bal is not None:
                    return float(getattr(bal, "balance", 0.0))
            return 0.0
        except Exception:
            return 0.0
