from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import asyncio
import json
import math
import time


def _safe_label(value: Any) -> str:
    """Return a short log-safe label without payload values."""
    raw = str(value)
    return "".join(ch for ch in raw if ch.isascii() and (ch.isalnum() or ch in "_./-"))[:64] or "unknown"


_AUTH_SUCCESS_EVENTS = frozenset({"successauth", "auth/success"})


def _is_auth_success_event(event_name: str) -> bool:
    """Recognize both the SDK and current browser authorization event names."""
    return _safe_label(event_name).lower() in _AUTH_SUCCESS_EVENTS


def _safe_payload_meta(data: Any) -> tuple[str, str, int]:
    """Summarize inbound data without logging any values."""
    payload_type = type(data).__name__
    keys = "none"
    if isinstance(data, dict):
        safe_keys = []
        for key in list(data.keys())[:12]:
            label = _safe_label(key)[:32]
            if label:
                safe_keys.append(label)
        keys = ",".join(safe_keys) or "none"
    try:
        if isinstance(data, (bytes, bytearray)):
            payload_bytes = len(data)
        elif isinstance(data, str):
            payload_bytes = len(data.encode("utf-8"))
        else:
            payload_bytes = len(json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    except Exception:
        payload_bytes = -1
    return payload_type, keys, payload_bytes


def _build_auth_data(
    authorization_data_type: type,
    session: str,
    uid: str,
    is_demo: bool,
    session_field: str = "sessionToken",
) -> Any:
    """Build the auth model using one explicitly selected session field."""
    payload: dict[str, Any] = {
        "isDemo": 1 if is_demo else 0,
        "uid": int(uid),
        "platform": 2,
        "isFastHistory": True,
        "isOptimized": True,
    }
    if session_field == "session":
        payload["session"] = session
        return authorization_data_type.model_validate(payload)
    if session_field == "sessionToken":
        from pydantic import Field

        class BrowserAuthorizationData(authorization_data_type):
            session: str = Field(..., alias="sessionToken")

        payload["sessionToken"] = session
        return BrowserAuthorizationData.model_validate(payload)
    raise ValueError("PO_AUTH_SESSION_FIELD must be session or sessionToken")


@dataclass
class PocketApiConfig:
    session: str
    uid: str
    is_demo: bool = True
    region: str = "DEMO"
    auth_session_field: str = "sessionToken"


class PocketOptionApiAdapter:
    """
    Unofficial API adapter.
    This wrapper is defensive: if SDK is not installed, it raises clear errors.
    """

    def __init__(self, cfg: PocketApiConfig):
        self.cfg = cfg
        self._client = None
        self._deals = None
        self._history_event = asyncio.Event()
        self._history_request: tuple[Any, int, int] | None = None
        self._history_rows: list[dict[str, Any]] = []
        self._request_index = 0
        self._history_lock = asyncio.Lock()

    async def _on_history(self, payload: Any) -> None:
        request = self._history_request
        if request is None:
            return
        asset, period, index = request
        if payload.asset != asset or payload.period != period:
            return
        if payload.index is not None and payload.index != index:
            return
        rows: dict[float, dict[str, Any]] = {}
        for row in payload.data:
            try:
                candle = {"time": float(row.time), "open": float(row.open),
                          "high": float(row.high), "low": float(row.low),
                          "close": float(row.close), "volume": float(row.volume)}
                if not all(math.isfinite(value) for value in candle.values()):
                    continue
                if candle["time"] <= 0 or candle["time"] > time.time() + period:
                    continue
                if min(candle[k] for k in ("open", "high", "low", "close")) <= 0:
                    continue
                if not (candle["low"] <= min(candle["open"], candle["close"])
                        <= max(candle["open"], candle["close"]) <= candle["high"]):
                    continue
                rows[candle["time"]] = candle
            except (TypeError, ValueError, OverflowError, AttributeError):
                continue
        self._history_rows = [rows[key] for key in sorted(rows)]
        self._history_event.set()

    async def connect(self) -> None:
        try:
            from pocket_option import PocketOptionClient  # type: ignore
            from pocket_option.models import AuthorizationData  # type: ignore
            from pocket_option.contrib.deals import MemoryDealsStorage  # type: ignore
        except Exception as e:
            raise RuntimeError("Install unofficial SDK: pip install pocket-option") from e

        if not self.cfg.session.strip() or not self.cfg.uid.strip():
            raise RuntimeError("API authentication requires PO_SESSION and PO_UID")

        class AuthDiagnosticClient(PocketOptionClient):
            """Observe SDK events using metadata only; never log payload values."""

            def __init__(self):
                self._auth_diag_started_at: float | None = None
                self._auth_diag_event_count = 0
                self._auth_diag_logged_count = 0
                self._auth_diag_disconnect_seen = False
                super().__init__()

            async def handle_new_event(self, event_name: str, data: Any = None):
                is_auth_success = _is_auth_success_event(event_name)
                if is_auth_success:
                    # The current site emits auth/success; SDK 0.4.0 waits for successauth.
                    self.authorized_event.set()
                started_at = self._auth_diag_started_at
                if started_at is not None:
                    self._auth_diag_event_count += 1
                    label = _safe_label(event_name)
                    important = is_auth_success or any(
                        marker in label.lower() for marker in ("auth", "error", "reject", "fail")
                    )
                    if self._auth_diag_logged_count < 30 and (self._auth_diag_event_count <= 20 or important):
                        self._auth_diag_logged_count += 1
                        payload_type, payload_keys, payload_bytes = _safe_payload_meta(data)
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        status = "success" if is_auth_success else "observed"
                        print(
                            f"[api] auth_response event={label} status={status} elapsed_ms={elapsed_ms} "
                            f"payload_type={_safe_label(payload_type)} payload_keys={payload_keys} "
                            f"payload_bytes={payload_bytes}",
                            flush=True,
                        )
                return await super().handle_new_event(event_name, data)

        print("[api] connect_stage=resolve_region status=started", flush=True)
        regions = __import__("pocket_option.constants", fromlist=["Regions"]).Regions
        region = getattr(regions, self.cfg.region, None)
        if region is None:
            raise RuntimeError("Unknown PO_REGION; use a supported SDK region")
        print("[api] connect_stage=resolve_region status=ok", flush=True)

        self._client = AuthDiagnosticClient()
        self._client.on.load_history_period_fast(self._on_history)

        async def _on_socket_disconnect() -> None:
            client = self._client
            if client is None:
                return
            client._auth_diag_disconnect_seen = True
            started_at = client._auth_diag_started_at
            if started_at is not None:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                print(f"[api] auth_socket status=closed elapsed_ms={elapsed_ms}", flush=True)

        self._client.on.disconnect(_on_socket_disconnect)
        print("[api] connect_stage=build_auth status=started", flush=True)
        auth = _build_auth_data(
            AuthorizationData,
            session=self.cfg.session,
            uid=self.cfg.uid,
            is_demo=self.cfg.is_demo,
            session_field=self.cfg.auth_session_field,
        )
        print(
            f"[api] connect_stage=build_auth status=ok auth_session_field={self.cfg.auth_session_field}",
            flush=True,
        )

        print("[api] connect_stage=socket_connect status=started", flush=True)
        await self._client.connect(region)
        print("[api] connect_stage=socket_connect status=ok", flush=True)

        print("[api] connect_stage=emit_auth status=started", flush=True)
        auth_started_at = time.monotonic()
        self._client._auth_diag_started_at = auth_started_at
        await self._client.emit.auth(auth)
        print("[api] connect_stage=emit_auth status=ok", flush=True)

        # A socket connection alone does not prove broker authentication.
        print("[api] connect_stage=wait_authorization status=started", flush=True)
        try:
            await self._client.wait_for_authorization(timeout=15.0)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - auth_started_at) * 1000)
            try:
                socket_connected: bool | str = bool(self._client.sio.connected)
            except Exception:
                socket_connected = "unknown"
            print(
                f"[api] auth_wait status=failed elapsed_ms={elapsed_ms} "
                f"error_type={_safe_label(type(exc).__name__)} message=authorization_wait_failed "
                f"socket_connected={socket_connected} "
                f"disconnect_seen={self._client._auth_diag_disconnect_seen}",
                flush=True,
            )
            raise
        else:
            print("[api] connect_stage=wait_authorization status=ok", flush=True)
        finally:
            self._client._auth_diag_started_at = None
        self._deals = MemoryDealsStorage(self._client)

    async def disconnect(self) -> None:
        # Clear references even when the SDK's partial-connection cleanup fails;
        # otherwise the runner can mistake a dead client for a reusable one.
        client = self._client
        self._client = None
        self._deals = None
        self._history_request = None
        self._history_rows = []
        self._history_event.clear()
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def get_candles(self, asset: str, timeframe_sec: int, count: int) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("API adapter is not connected")
        from pocket_option.models import Asset, ChangeAssetRequest, LoadHistoryPeriodRequest

        if timeframe_sec <= 0 or count <= 0:
            raise ValueError("Candle period and count must be positive")
        # The SDK can manufacture unknown enum values; fail closed instead.
        sdk_asset = Asset.__members__.get(asset)
        if sdk_asset is None:
            sdk_asset = next((member for member in Asset if member.value == asset), None)
        if sdk_asset is None:
            raise RuntimeError("Configured asset is unsupported by the SDK")
        # pocket-option is event-driven: these methods are on client.emit,
        # not guessed get_candles/history methods on the client itself.
        async with self._history_lock:
            self._request_index += 1
            index = self._request_index
            self._history_request = (sdk_asset, timeframe_sec, index)
            self._history_rows = []
            self._history_event.clear()
            try:
                await self._client.emit.subscribe_to_asset(sdk_asset)
                await self._client.emit.change_asset(ChangeAssetRequest(asset=sdk_asset, period=timeframe_sec))
                await self._client.emit.load_history_period(LoadHistoryPeriodRequest(
                    asset=sdk_asset, index=index, time=time.time(),
                    offset=timeframe_sec * count, period=timeframe_sec,
                ))
                await asyncio.wait_for(self._history_event.wait(), timeout=20.0)
                if not self._history_rows:
                    raise RuntimeError("Broker history response contains no valid OHLC candles")
                return self._history_rows[-count:]
            except asyncio.TimeoutError:
                raise RuntimeError("Broker candle history response timed out") from None
            finally:
                self._history_request = None

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
