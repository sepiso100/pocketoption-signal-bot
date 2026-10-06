from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


_ZAMBIA = ZoneInfo("Africa/Lusaka")


@dataclass
class RiskConfig:
    min_payout_pct: float
    max_signal_age_ms: int
    max_consecutive_losses: int
    max_trades_per_day: int
    daily_loss_stop_pct: float


class RiskManager:
    """Risk limits persisted to disk; corrupt state fails closed."""

    def __init__(self, cfg: RiskConfig, start_balance: float, state_path: str = "data/risk_state.json") -> None:
        self.cfg = cfg
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.day = datetime.now(_ZAMBIA).date()
        self.day_start_balance = float(start_balance) if start_balance > 0 else 0.0
        self.consecutive_losses = 0
        self.trades_today = 0
        self.halted_reason = ""
        self.pending_order = False
        self.last_order_candle: str | None = None
        self._loaded_today = False
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            saved_day = date.fromisoformat(data["day"])
            start_balance = float(data["day_start_balance"])
            consecutive = int(data["consecutive_losses"])
            trades = int(data["trades_today"])
            pending = bool(data.get("pending_order", False))
            last_candle = data.get("last_order_candle")
            if last_candle is not None and not isinstance(last_candle, str):
                raise ValueError("invalid last candle key")
            halt = str(data.get("halted_reason", ""))[:64]
            if not math.isfinite(start_balance) or start_balance < 0 or consecutive < 0 or trades < 0:
                raise ValueError("invalid risk state values")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise RuntimeError("Risk state is unreadable; trading is blocked until it is inspected.") from None
        if saved_day > self.day:
            raise RuntimeError("Risk state date is in the future; trading is blocked until inspected.")
        self.pending_order = pending
        self.last_order_candle = last_candle
        if saved_day == self.day:
            self.day_start_balance = start_balance
            self.consecutive_losses = consecutive
            self.trades_today = trades
            self.halted_reason = halt
            self._loaded_today = True
        elif pending:
            self.halted_reason = "pending_trade_reconciliation"

    def _save(self) -> None:
        payload = {
            "day": self.day.isoformat(),
            "day_start_balance": self.day_start_balance,
            "consecutive_losses": self.consecutive_losses,
            "trades_today": self.trades_today,
            "halted_reason": self.halted_reason,
            "pending_order": self.pending_order,
            "last_order_candle": self.last_order_candle,
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)

    def set_verified_balance(self, balance: float) -> None:
        balance = float(balance)
        if not math.isfinite(balance) or balance <= 0:
            raise RuntimeError("Broker balance is not verified; trading is blocked.")
        today = datetime.now(_ZAMBIA).date()
        if today != self.day:
            self.day = today
            self.day_start_balance = balance
            self.consecutive_losses = 0
            self.trades_today = 0
            if not self.pending_order:
                self.halted_reason = ""
        elif self.day_start_balance <= 0:
            self.day_start_balance = balance
        if self.halted_reason == "balance_unverified" and not self.pending_order:
            self.halted_reason = ""
        self._save()

    def _roll_day_if_needed(self, current_balance: float) -> None:
        today = datetime.now(_ZAMBIA).date()
        if today != self.day and current_balance > 0:
            self.day = today
            self.day_start_balance = current_balance
            self.consecutive_losses = 0
            self.trades_today = 0
            self.halted_reason = "pending_trade_reconciliation" if self.pending_order else ""
            self._save()

    def can_trade(
        self, payout_pct: float, signal_age_ms: int, current_balance: float,
        candle_key: str | None = None,
    ) -> tuple[bool, str]:
        self._roll_day_if_needed(current_balance)
        if self.pending_order:
            return False, "pending_trade_reconciliation"
        if candle_key is not None and candle_key == self.last_order_candle:
            return False, "already_traded_this_candle"
        if self.halted_reason:
            return False, self.halted_reason
        if not math.isfinite(current_balance) or current_balance <= 0:
            return False, "balance_unverified"
        if not math.isfinite(payout_pct) or payout_pct <= 0 or payout_pct >= 100:
            return False, "payout_unverified"
        if self.day_start_balance <= 0:
            return False, "balance_unverified"
        pnl_pct = ((current_balance - self.day_start_balance) / self.day_start_balance) * 100
        if pnl_pct <= -self.cfg.daily_loss_stop_pct:
            self.halted_reason = "daily_loss_stop"
            self._save()
            return False, self.halted_reason
        if self.trades_today >= self.cfg.max_trades_per_day:
            return False, "max_trades_per_day"
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            return False, "max_consecutive_losses"
        if payout_pct < self.cfg.min_payout_pct:
            return False, "payout_below_min"
        if signal_age_ms < 0 or signal_age_ms > self.cfg.max_signal_age_ms:
            return False, "signal_too_old"
        return True, "ok"

    def mark_pending(self, candle_key: str | None = None) -> None:
        if self.pending_order:
            raise RuntimeError("A previous order needs reconciliation; refusing another order.")
        self.pending_order = True
        if candle_key is not None:
            self.last_order_candle = str(candle_key)
        self._save()

    def mark_uncertain(self, reason: str = "order_outcome_uncertain") -> None:
        self.pending_order = True
        self.halted_reason = reason[:64]
        self._save()

    def halt(self, reason: str) -> None:
        self.halted_reason = reason[:64]
        self._save()

    def register_result(self, won: bool) -> None:
        self.trades_today += 1
        self.consecutive_losses = 0 if won else self.consecutive_losses + 1
        self.pending_order = False
        self._save()
