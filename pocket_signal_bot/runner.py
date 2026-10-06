from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

from pocket_signal_bot.adapters.api_adapter import PocketApiConfig, PocketOptionApiAdapter
from pocket_signal_bot.adapters.browser_adapter import BrowserConfig, PocketOptionBrowserAdapter
from pocket_signal_bot.config import BotConfig, validate_config
from pocket_signal_bot.control import ControlServer, ControlState
from pocket_signal_bot.logger import JsonEventLogger
from pocket_signal_bot.paper_simulator import PocketPaperSimulator
from pocket_signal_bot.risk import RiskConfig, RiskManager
from pocket_signal_bot.strategy import EmaRsiStrategy, StrategyConfig
from pocket_signal_bot.trade_store import TradeStore

try:
    from pocket_signal_bot.charts import generate_charts
except ImportError:
    generate_charts = None  # type: ignore[misc, assignment]


class HybridRunner:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.control = ControlState(mode=cfg.effective_mode)
        self.control_server = ControlServer(self.control)
        self.logger = JsonEventLogger(console=cfg.po_console_log)
        self.strategy = EmaRsiStrategy(
            StrategyConfig(
                ema_fast=cfg.ema_fast,
                ema_slow=cfg.ema_slow,
                rsi_period=cfg.rsi_period,
                buy_rsi_min=cfg.buy_rsi_min,
                sell_rsi_max=cfg.sell_rsi_max,
                rsi_neutral_band=cfg.rsi_neutral_band,
                min_ema_gap=cfg.min_ema_gap,
                require_momentum_confirm=cfg.require_momentum_confirm,
                min_abs_ema_diff=cfg.min_abs_ema_diff,
                allow_fallback_vote=cfg.allow_fallback_vote,
                min_trend_streak=cfg.min_trend_streak,
                chop_lookback=cfg.chop_lookback,
                min_range_pct=cfg.min_range_pct,
            )
        )
        self.paper = PocketPaperSimulator()
        self.api = PocketOptionApiAdapter(
            PocketApiConfig(
                session=cfg.po_session,
                uid=cfg.po_uid,
                is_demo=cfg.api_is_demo,
                region=cfg.po_region,
            )
        )
        self.browser = PocketOptionBrowserAdapter(
            BrowserConfig(
                base_url=cfg.browser_base_url,
                is_real_account=(cfg.effective_mode == "live"),
                headless=cfg.po_headless,
                price_selectors=tuple(cfg.price_selector_list),
                payout_selectors=tuple(cfg.payout_selector_list),
                startup_wait_ms=max(1000, cfg.po_browser_startup_wait_sec * 1000),
                quote_asset=cfg.symbol,
                use_ws_quotes=cfg.po_use_ws_quotes,
                show_overlay=cfg.po_browser_overlay,
                ws_debug=cfg.po_ws_debug,
            )
        )
        initial_balance = 0.0 if cfg.requires_broker else 1000.0
        self.risk = RiskManager(
            RiskConfig(
                min_payout_pct=cfg.min_payout_pct,
                max_signal_age_ms=cfg.max_signal_age_ms,
                max_consecutive_losses=cfg.max_consecutive_losses,
                max_trades_per_day=cfg.max_trades_per_day,
                daily_loss_stop_pct=cfg.daily_loss_stop_pct,
            ),
            start_balance=initial_balance,
            state_path=cfg.risk_state_path,
        )
        self.balance = initial_balance
        self._paper_price = 1.0800
        self._api_candles_disabled = False
        self._api_candles_fail_count = 0
        self._api_connected = False
        self._browser_connected = False
        # Signal confirmation / flip-cooldown state
        self._prev_raw_signal: str | None = None
        self._flip_cooldown_until: float = 0.0
        self._confirm_streak_side: str | None = None
        self._confirm_streak_count: int = 0
        # Session stats (since process start): realized PnL from settled trades + win rate
        self._session_pnl: float = 0.0
        self._session_trades: int = 0
        # Wins/losses for stats: profit > 0 / profit < 0 (pushes excluded from win rate)
        self._session_wins: int = 0
        self._session_losses: int = 0
        self._session_pushes: int = 0
        self.trade_store = TradeStore(cfg.trade_data_path)
        self._session_id = self.trade_store.begin_session(self._session_config_snapshot())

    def _session_config_snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.cfg.effective_mode,
            "symbol": self.cfg.symbol,
            "timeframe_sec": self.cfg.timeframe_sec,
            "expiry_sec": self.cfg.expiry_sec,
            "time_pair": self.cfg.time_pair_label,
            "experiment_label": self.cfg.experiment_label or self.cfg.time_pair_label,
            "trade_amount": self.cfg.trade_amount,
        }

    def _persist_trade(
        self,
        *,
        signal: str,
        pnl: float,
        order_id: str,
        payout_pct: float,
        amount: float,
        adapter: str,
        result_ts: str,
    ) -> None:
        self.trade_store.record_trade(
            {
                "ts": result_ts,
                "side": "buy" if signal == "CALL" else "sell",
                "signal": signal,
                "order_id": order_id,
                "amount": amount,
                "payout_pct": payout_pct,
                "pnl": round(float(pnl), 4),
                "won": float(pnl) > 0,
                "push": float(pnl) == 0,
                "adapter": adapter,
            }
        )
        if self.cfg.charts_auto and generate_charts is not None:
            try:
                paths = generate_charts(self.cfg.trade_data_path, self.cfg.charts_dir)
                if paths:
                    print(f"[charts] updated {paths[-1]}", flush=True)
            except Exception as e:
                print(f"[charts] skip: {e}", flush=True)

    def _finalize_charts(self) -> None:
        self.trade_store.end_session()
        if generate_charts is None:
            return
        try:
            paths = generate_charts(self.cfg.trade_data_path, self.cfg.charts_dir)
            if paths:
                print("[charts] saved:", flush=True)
                for p in paths:
                    print(f"  {p.resolve()}", flush=True)
        except Exception as e:
            print(f"[charts] failed: {e}", flush=True)

    @staticmethod
    def _normalize_settled_pnl(result: dict[str, Any], trade_amount: float, payout_pct: float) -> float:
        """Single realized P&L number per trade (stake loss = negative, win = payout on stake, push = 0)."""
        amt = float(trade_amount)
        pay = float(payout_pct)
        status = str(result.get("result", "") or "").lower()
        if status not in {"win", "loss", "draw"}:
            raise RuntimeError("Settlement is unverified; reconciliation is required.")
        if status == "draw":
            return 0.0
        raw = result.get("pnl")
        if raw is not None and isinstance(raw, (int, float)):
            pnl = float(raw)
            if not math.isfinite(pnl):
                raise RuntimeError("Settlement P&L is invalid; reconciliation is required.")
            if result.get("settlement_source") == "balance":
                return pnl
            if pnl == 0.0 and status == "loss":
                return -amt
            return pnl
        if status == "win":
            return amt * (pay / 100.0)
        return -amt

    @staticmethod
    def _timestamp_seconds(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            try:
                stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                return None
        if not math.isfinite(stamp):
            return None
        if stamp > 1e12:
            stamp /= 1000.0
        return stamp if stamp >= 1e9 else None

    def _record_session_trade(self, *, pnl: float) -> dict[str, Any]:
        pnl = float(pnl)
        self._session_pnl += pnl
        self._session_trades += 1
        if pnl > 0:
            self._session_wins += 1
        elif pnl < 0:
            self._session_losses += 1
        else:
            self._session_pushes += 1
        decided = self._session_wins + self._session_losses
        wr: float | None
        if decided > 0:
            wr = round((self._session_wins / decided) * 100.0, 2)
        else:
            wr = None
        return {
            "session_pnl": round(self._session_pnl, 4),
            "session_trades": self._session_trades,
            "session_wins": self._session_wins,
            "session_losses": self._session_losses,
            "session_pushes": self._session_pushes,
            "win_rate_pct": wr,
        }

    def _session_overlay_line(self) -> str:
        n = self._session_trades
        if n == 0:
            return "Session PnL: +0.00  |  trades: 0  |  win rate: —"
        decided = self._session_wins + self._session_losses
        wr_s = f"{self._session_wins / decided * 100:.1f}%" if decided else "—"
        return (
            f"Session PnL: {self._session_pnl:+.2f}  |  trades: {n}  "
            f"(W{self._session_wins}/L{self._session_losses}/P{self._session_pushes})  |  win rate: {wr_s}"
        )

    # ── Signal post-processing ───────────────────────────────────────────────

    def _finalize_signal(self, raw: str) -> tuple[str, dict[str, Any]]:
        """Apply flip-cooldown and consecutive-poll confirmation on top of strategy output."""
        meta: dict[str, Any] = {"raw_signal": raw}
        now = time.time()

        if raw in ("CALL", "PUT"):
            if (
                self.cfg.flip_cooldown_sec > 0
                and self._prev_raw_signal in ("CALL", "PUT")
                and self._prev_raw_signal != raw
            ):
                self._flip_cooldown_until = now + float(self.cfg.flip_cooldown_sec)
            self._prev_raw_signal = raw

            if raw == self._confirm_streak_side:
                self._confirm_streak_count += 1
            else:
                self._confirm_streak_side = raw
                self._confirm_streak_count = 1

            need = self.cfg.signal_confirm_polls
            confirmed: str = raw if self._confirm_streak_count >= need else "NO_TRADE"
            meta["confirm_streak"] = self._confirm_streak_count
            meta["confirm_need"] = need
        else:
            self._confirm_streak_side = None
            self._confirm_streak_count = 0
            confirmed = "NO_TRADE"
            meta["confirm_streak"] = 0
            meta["confirm_need"] = self.cfg.signal_confirm_polls

        meta["flip_cooldown_until"] = self._flip_cooldown_until
        if confirmed in ("CALL", "PUT") and now < self._flip_cooldown_until:
            meta["flip_cooldown_active"] = True
            return "NO_TRADE", meta
        meta["flip_cooldown_active"] = now < self._flip_cooldown_until
        return confirmed, meta

    # ── Browser overlay ──────────────────────────────────────────────────────

    async def _refresh_browser_overlay(
        self,
        *,
        signal: str,
        payout: float,
        trade_amount: float,
        can_trade: bool,
        reason: str,
        candles_adapter: str,
        last_close: float | None,
        extra: str = "",
    ) -> None:
        if not self.cfg.requires_broker or not self.cfg.po_browser_overlay:
            return
        lq = getattr(self.browser, "last_ws_quote", None)
        lines = [
            f"PocketOption bot  |  {self.cfg.effective_mode.upper()}",
            f"Symbol: {self.cfg.symbol}  |  amount: {trade_amount}",
            f"Signal: {signal}  |  payout: {payout}%",
            f"Trade allowed: {'YES' if can_trade else 'NO'} ({reason})",
            f"Candles source: {candles_adapter}",
            f"Last close (series): {last_close if last_close is not None else '—'}",
            f"Last WS quote: {lq if lq is not None else '—'}",
            f"Balance (bot): {self.balance:.2f}",
            self._session_overlay_line(),
        ]
        if extra:
            lines.append(extra)
        try:
            await self.browser.show_status_overlay("\n".join(lines))
        except Exception:
            pass

    # ── Paper candle generator ───────────────────────────────────────────────

    def _paper_candles(self) -> list[dict[str, Any]]:
        candles: list[dict[str, Any]] = []
        price = self._paper_price
        for i in range(self.cfg.candle_count):
            drift = 0.00002 if i % 7 < 4 else -0.00001
            price = max(0.5, price + drift)
            candles.append({"close": round(price, 5)})
        self._paper_price = price
        return candles

    # ── Adapter connect / disconnect ─────────────────────────────────────────

    async def _safe_connect(self) -> None:
        if not self.cfg.requires_broker:
            return
        timeout = float(self.cfg.connect_timeout_sec)
        if self.cfg.skip_api_connect:
            self.logger.log("adapter_connect", adapter="api", ok=False, reason="disabled_by_config")
        else:
            try:
                await asyncio.wait_for(self.api.connect(), timeout=timeout)
                self._api_connected = True
                self.logger.log("adapter_connect", adapter="api", ok=True)
            except Exception as exc:
                self._api_connected = False
                self.logger.log("adapter_connect", adapter="api", ok=False, error=type(exc).__name__)
                try:
                    await asyncio.wait_for(self.api.disconnect(), timeout=5.0)
                except Exception:
                    pass

        # Live execution is API-only. The browser flow cannot verify account,
        # symbol, expiry, or stake and must never click a real-money order.
        if self.cfg.effective_mode == "live" and not self._api_connected:
            raise RuntimeError("Live trading blocked: broker API is unavailable; browser order fallback is disabled.")

        if self.cfg.effective_mode != "live":
            try:
                await asyncio.wait_for(self.browser.connect(), timeout=timeout)
                self._browser_connected = True
                self.logger.log("adapter_connect", adapter="browser", ok=True)
            except Exception as exc:
                self._browser_connected = False
                self.logger.log("adapter_connect", adapter="browser", ok=False, error=type(exc).__name__)

        bal_timeout = min(15.0, max(5.0, timeout / 4))
        adapters = [("api", self.api)]
        if self.cfg.effective_mode != "live":
            adapters.append(("browser", self.browser))
        verified = False
        for adapter_name, adapter in adapters:
            try:
                bal = await asyncio.wait_for(adapter.get_balance(), timeout=bal_timeout)
                if bal > 0:
                    self.balance = float(bal)
                    self.risk.set_verified_balance(self.balance)
                    self.logger.log("balance_init", adapter=adapter_name, balance=bal)
                    verified = True
                    break
            except Exception:
                continue
        if not verified:
            raise RuntimeError("Broker balance could not be verified; trading is blocked.")
        self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
        self.logger.log("connect_complete", mode=self.cfg.effective_mode, balance_verified=True)

    async def _safe_disconnect(self) -> None:
        for adapter_name, adapter in (("api", self.api), ("browser", self.browser)):
            try:
                await adapter.disconnect()
                if adapter_name == "api":
                    self._api_connected = False
                else:
                    self._browser_connected = False
                self.logger.log("adapter_disconnect", adapter=adapter_name, ok=True)
            except Exception as e:
                self.logger.log("adapter_disconnect", adapter=adapter_name, ok=False, error=str(e))

    # ── Data helpers ─────────────────────────────────────────────────────────

    async def _api_call(self, coro: Any, *, what: str) -> Any:
        """Wrap any SDK awaitable with a hard timeout so a stuck call can't freeze the loop."""
        try:
            return await asyncio.wait_for(coro, timeout=float(self.cfg.data_timeout_sec))
        except asyncio.TimeoutError as e:
            self.logger.log("api_timeout", what=what, sec=self.cfg.data_timeout_sec)
            raise RuntimeError(f"API {what} timed out after {self.cfg.data_timeout_sec}s") from e

    async def _get_candles_with_failover(self) -> tuple[list[dict[str, Any]], str]:
        if self.cfg.effective_mode == "paper":
            return self._paper_candles(), "paper"
        if self._api_connected and not self._api_candles_disabled:
            try:
                candles = await self._api_call(
                    self.api.get_candles(self.cfg.symbol, self.cfg.timeframe_sec, self.cfg.candle_count),
                    what="get_candles",
                )
                self._api_candles_fail_count = 0
                return candles, "api"
            except Exception as e:
                self._api_connected = False
                self._api_candles_fail_count += 1
                self.logger.log("data_failover", from_adapter="api", to_adapter="browser", error=str(e))
                if self._api_candles_fail_count >= 3:
                    self._api_candles_disabled = True
                    self.logger.log(
                        "api_candles_disabled",
                        reason="repeated_api_candle_failures",
                        fail_count=self._api_candles_fail_count,
                    )
        try:
            browser_cap = float(self.cfg.data_timeout_sec) + 50.0
            candles = await asyncio.wait_for(
                self.browser.get_candles(self.cfg.symbol, self.cfg.timeframe_sec, self.cfg.candle_count),
                timeout=browser_cap,
            )
            return candles, "browser"
        except Exception as e2:
            self.logger.log("data_error", adapter="browser", error=str(e2))
            return [], "error"

    async def _get_payout_with_failover(self) -> tuple[float, str]:
        if self.cfg.effective_mode == "paper":
            return max(self.cfg.min_payout_pct, 80.0), "paper"
        if self._api_connected:
            try:
                payout = float(await self._api_call(self.api.get_payout_pct(self.cfg.symbol), what="get_payout_pct"))
                if 0 < payout < 100:
                    return payout, "api"
                raise RuntimeError("invalid payout")
            except Exception as exc:
                # A payout lookup failure is not an API disconnect, and must not
                # be replaced by a guessed percentage.
                self.logger.log("payout_unverified", adapter="api", error=type(exc).__name__)
        if self.cfg.effective_mode != "live" and self._browser_connected:
            try:
                payout = float(await self.browser.get_payout_pct(self.cfg.symbol))
                if 0 < payout < 100:
                    return payout, "browser"
            except Exception as exc:
                self.logger.log("payout_unverified", adapter="browser", error=type(exc).__name__)
        return 0.0, "unverified"

    async def _place_with_failover(
        self, direction: str, amount: float, candle_key: str | None = None,
    ) -> tuple[str, str]:
        if not self.cfg.requires_broker:
            return "paper-order", "paper"
        if not self._api_connected:
            raise RuntimeError("Broker API is unavailable; refusing browser order execution.")
        self.risk.mark_pending(candle_key)
        self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
        try:
            order_id = await self._api_call(
                self.api.place_order(self.cfg.symbol, amount, direction, self.cfg.expiry_sec),
                what="place_order",
            )
        except Exception as exc:
            # Submission may have reached the broker before timing out. Never
            # submit again through a second adapter without reconciliation.
            self.logger.log("order_submission_uncertain", error=type(exc).__name__)
            raise RuntimeError("Order submission is uncertain; reconcile before retrying.") from None
        if not order_id:
            raise RuntimeError("Broker returned no order id; reconcile before retrying.")
        return str(order_id), "api"

    async def _refresh_balance(self) -> bool:
        """Refresh broker balance; never treat an old/synthetic balance as verified."""
        bal_timeout = 10.0
        adapters = [(self.api, "api")]
        if self.cfg.effective_mode != "live" and self._browser_connected:
            adapters.append((self.browser, "browser"))
        for adapter, name in adapters:
            try:
                bal = await asyncio.wait_for(adapter.get_balance(), timeout=bal_timeout)
                if bal > 0:
                    self.balance = float(bal)
                    return True
            except Exception:
                continue
        self.logger.log("balance_unverified", mode=self.cfg.effective_mode)
        return False

    # ── Main loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self.logger.log(
            "startup",
            effective_mode=self.cfg.effective_mode,
            api_is_demo=self.cfg.api_is_demo,
            requires_broker=self.cfg.requires_broker,
        )
        self.control_server.start()
        await self._safe_connect()
        if self.cfg.requires_broker and self.cfg.po_browser_overlay:
            await self._refresh_browser_overlay(
                signal="—",
                payout=0.0,
                trade_amount=self.cfg.trade_amount,
                can_trade=False,
                reason="starting",
                candles_adapter="—",
                last_close=None,
                extra="Log in if needed. WS quotes will populate shortly.",
            )
        try:
            while True:
                candles, candles_adapter = await self._get_candles_with_failover()
                if not candles:
                    self.logger.log("no_candles", adapter=candles_adapter)
                    await self._refresh_browser_overlay(
                        signal="NO_TRADE",
                        payout=0.0,
                        trade_amount=self.cfg.trade_amount,
                        can_trade=False,
                        reason="no_candles",
                        candles_adapter=candles_adapter,
                        last_close=None,
                        extra="Waiting for candle data…",
                    )
                    await asyncio.sleep(self.cfg.poll_seconds)
                    continue

                # Reject malformed values and retain the latest source timestamp.
                closes: list[float] = []
                latest_candle_ts: float | None = None
                for c in candles:
                    try:
                        close = float(c["close"])
                        if not math.isfinite(close):
                            continue
                        closes.append(close)
                        latest_candle_ts = self._timestamp_seconds(c.get("time"))
                    except (KeyError, TypeError, ValueError):
                        continue
                candle_key = str(int(latest_candle_ts)) if latest_candle_ts is not None else None
                if not closes:
                    await asyncio.sleep(self.cfg.poll_seconds)
                    continue

                signal_info = self.strategy.generate_details(closes)
                raw_signal = str(signal_info["signal"])
                signal, sig_meta = self._finalize_signal(raw_signal)

                # signal_ts measured AFTER data fetch — reflects true freshness for risk gate
                signal_ts = datetime.now(timezone.utc)
                payout, payout_adapter = await self._get_payout_with_failover()
                trade_amount = float(self.cfg.trade_amount)
                signal_age_ms = int((datetime.now(timezone.utc) - signal_ts).total_seconds() * 1000)
                can_trade, reason = self.risk.can_trade(
                    payout_pct=payout,
                    signal_age_ms=signal_age_ms,
                    current_balance=self.balance,
                    candle_key=candle_key,
                )
                if self.cfg.requires_broker and latest_candle_ts is None:
                    can_trade, reason = False, "candle_timestamp_unverified"
                elif self.cfg.requires_broker and latest_candle_ts is not None:
                    max_age = self.cfg.max_candle_age_sec or max(30, self.cfg.timeframe_sec * 2)
                    candle_age = datetime.now(timezone.utc).timestamp() - latest_candle_ts
                    if candle_age < -1 or candle_age > max_age:
                        can_trade, reason = False, "candle_stale"
                if self.cfg.requires_broker and not self._api_connected:
                    can_trade, reason = False, "broker_api_unavailable"
                self.control.update_risk(self.risk.halted_reason or (reason if reason != "ok" else ""), self.risk.pending_order)
                if not self.control.is_order_allowed():
                    can_trade = False
                    reason = "stopped" if self.control.status()["stopped"] else "paused"
                self.logger.log(
                    "signal",
                    signal=signal,
                    raw_signal=sig_meta.get("raw_signal"),
                    confirm_streak=sig_meta.get("confirm_streak"),
                    confirm_need=sig_meta.get("confirm_need"),
                    flip_cooldown_active=sig_meta.get("flip_cooldown_active"),
                    payout_pct=payout,
                    can_trade=can_trade,
                    reason=reason,
                    candles_adapter=candles_adapter,
                    payout_adapter=payout_adapter,
                    ema_diff=round(float(signal_info.get("ema_diff", 0.0)), 8),
                    rsi=round(float(signal_info.get("rsi", 50.0)), 4),
                    momentum=round(float(signal_info.get("momentum", 0.0)), 8),
                    amount=trade_amount,
                )
                await self._refresh_browser_overlay(
                    signal=signal,
                    payout=payout,
                    trade_amount=trade_amount,
                    can_trade=can_trade,
                    reason=reason,
                    candles_adapter=candles_adapter,
                    last_close=closes[-1],
                )

                if signal in {"CALL", "PUT"} and can_trade:
                    try:
                        order_id, adapter_used = await self._place_with_failover(signal, trade_amount, candle_key)
                    except Exception as exc:
                        if self.cfg.requires_broker:
                            self.risk.mark_uncertain("order_submission_uncertain")
                            self.control.command("stop")
                            self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
                        self.logger.log("order_error", signal=signal, error=type(exc).__name__)
                        await asyncio.sleep(self.cfg.poll_seconds)
                        continue

                    send_ts = datetime.now(timezone.utc)
                    self.logger.log(
                        "order_sent",
                        order_id=order_id,
                        adapter=adapter_used,
                        signal=signal,
                        amount=trade_amount,
                    )
                    await self._refresh_browser_overlay(
                        signal=signal,
                        payout=payout,
                        trade_amount=trade_amount,
                        can_trade=True,
                        reason="order_sent",
                        candles_adapter=candles_adapter,
                        last_close=closes[-1],
                        extra=f"ORDER → {adapter_used} id={order_id} amount={trade_amount}",
                    )

                    if self.cfg.effective_mode == "paper":
                        entry = closes[-1]
                        await asyncio.sleep(self.cfg.expiry_sec)
                        candles2, _ = await self._get_candles_with_failover()
                        if not candles2:
                            await asyncio.sleep(self.cfg.poll_seconds)
                            continue
                        exit_price = float(candles2[-1]["close"])
                        result = self.paper.settle(
                            direction=signal,
                            amount=trade_amount,
                            payout_pct=payout,
                            entry_price=entry,
                            exit_price=exit_price,
                        )
                        pnl = float(result.pnl)
                        self.balance += pnl
                        self.risk.register_result(pnl >= 0)
                        sess = self._record_session_trade(pnl=pnl)
                        self._persist_trade(
                            signal=signal,
                            pnl=pnl,
                            order_id=order_id,
                            payout_pct=payout,
                            amount=trade_amount,
                            adapter="paper",
                            result_ts=datetime.now(timezone.utc).isoformat(),
                        )
                        self.logger.log(
                            "order_result",
                            mode="paper",
                            order_id=order_id,
                            won=pnl > 0,
                            push=pnl == 0,
                            pnl=round(pnl, 4),
                            balance=round(self.balance, 2),
                            signal_ts=signal_ts.isoformat(),
                            send_ts=send_ts.isoformat(),
                            result_ts=datetime.now(timezone.utc).isoformat(),
                            amount=trade_amount,
                            **sess,
                        )
                        await self._refresh_browser_overlay(
                            signal=signal,
                            payout=payout,
                            trade_amount=trade_amount,
                            can_trade=True,
                            reason="order_done",
                            candles_adapter=candles_adapter,
                            last_close=closes[-1],
                            extra=f"RESULT outcome={'win' if pnl > 0 else 'loss' if pnl < 0 else 'push'} pnl={pnl:+.2f}",
                        )
                    else:
                        try:
                            if adapter_used == "api":
                                result = await self._api_call(
                                    self.api.check_result(order_id, self.cfg.expiry_sec),
                                    what="check_result",
                                )
                            else:
                                result = await self.browser.check_result(order_id, self.cfg.expiry_sec)
                        except Exception as exc:
                            self.risk.mark_uncertain("settlement_unverified")
                            self.control.command("stop")
                            self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
                            self.logger.log("result_error", order_id=order_id, error=type(exc).__name__)
                            await asyncio.sleep(self.cfg.poll_seconds)
                            continue

                        try:
                            pnl = self._normalize_settled_pnl(result, trade_amount, payout)
                        except Exception as exc:
                            self.risk.mark_uncertain("settlement_unverified")
                            self.control.command("stop")
                            self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
                            self.logger.log("result_unverified", order_id=order_id, error=type(exc).__name__)
                            await asyncio.sleep(self.cfg.poll_seconds)
                            continue
                        self.balance += float(pnl)
                        self.risk.register_result(float(pnl) >= 0)
                        sess = self._record_session_trade(pnl=float(pnl))
                        self._persist_trade(
                            signal=signal,
                            pnl=float(pnl),
                            order_id=order_id,
                            payout_pct=payout,
                            amount=trade_amount,
                            adapter=adapter_used,
                            result_ts=datetime.now(timezone.utc).isoformat(),
                        )
                        # Balance must be re-verified after each broker settlement.
                        if not await self._refresh_balance():
                            self.risk.halt("balance_unverified")
                            self.control.command("stop")
                        self.control.update_risk(self.risk.halted_reason, self.risk.pending_order)
                        self.logger.log(
                            "order_result",
                            mode=self.cfg.effective_mode,
                            order_id=order_id,
                            won=float(pnl) > 0,
                            push=float(pnl) == 0,
                            pnl=round(float(pnl), 4),
                            balance=round(self.balance, 2),
                            signal_ts=signal_ts.isoformat(),
                            send_ts=send_ts.isoformat(),
                            result_ts=datetime.now(timezone.utc).isoformat(),
                            amount=trade_amount,
                            **sess,
                        )
                        await self._refresh_browser_overlay(
                            signal=signal,
                            payout=payout,
                            trade_amount=trade_amount,
                            can_trade=True,
                            reason="order_done",
                            candles_adapter=candles_adapter,
                            last_close=closes[-1],
                            extra=(
                                f"RESULT outcome={'win' if float(pnl) > 0 else 'loss' if float(pnl) < 0 else 'push'} "
                                f"pnl={float(pnl):+.2f}"
                            ),
                        )

                await asyncio.sleep(self.cfg.poll_seconds)
        finally:
            self._finalize_charts()
            await self._safe_disconnect()
            self.control_server.close()


async def main() -> None:
    print("pocket_signal_bot: loading config…", flush=True)
    cfg = BotConfig()
    validate_config(cfg)
    print(
        f"pocket_signal_bot: starting in {cfg.effective_mode.upper()} mode "
        f"(connect timeout {cfg.connect_timeout_sec:g}s)…",
        flush=True,
    )
    runner = HybridRunner(cfg)
    await runner.run()


if __name__ == "__main__":
    print("pocket_signal_bot: launch", flush=True)
    asyncio.run(main())
