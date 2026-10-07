from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any


class JsonEventLogger:
    """Console-only event lines (no file logging)."""

    def __init__(self, *, console: bool = True) -> None:
        self.console = console

    def _print_console(self, event_type: str, payload: dict[str, Any]) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if event_type == "startup":
            raw_sha = payload.get("source_sha", "unknown")
            source_sha = str(raw_sha).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{7,40}", source_sha):
                source_sha = "unknown"
            print(
                f"[{ts}] START mode={payload.get('effective_mode')} "
                f"api_demo={payload.get('api_is_demo')} broker={payload.get('requires_broker')} "
                f"source_sha={source_sha}",
                flush=True,
            )
        elif event_type == "signal":
            ok = payload.get("can_trade")
            print(
                f"[{ts}] SIGNAL {payload.get('signal')} | payout={payload.get('payout_pct')}% "
                f"| trade={'YES' if ok else 'NO'} ({payload.get('reason')}) "
                f"| amount={payload.get('amount')} "
                f"| candles={payload.get('candles_adapter')} payout_src={payload.get('payout_adapter')} "
                f"| ema_diff={payload.get('ema_diff')} rsi={payload.get('rsi')} momentum={payload.get('momentum')}",
                flush=True,
            )
        elif event_type == "order_sent":
            print(
                f"[{ts}] ORDER SENT {payload.get('signal')} adapter={payload.get('adapter')} "
                f"amount={payload.get('amount', '—')} "
                f"id={payload.get('order_id')}",
                flush=True,
            )
        elif event_type == "order_result":
            st = int(payload.get("session_trades") or 0)
            spnl = payload.get("session_pnl")
            sw = payload.get("session_wins")
            sl = payload.get("session_losses")
            sp = payload.get("session_pushes")
            wr = payload.get("win_rate_pct")
            extra = ""
            if st > 0 and spnl is not None and sw is not None and sl is not None and sp is not None:
                wr_s = f"{wr}%" if wr is not None else "—"
                extra = (
                    f" | session PnL={spnl:+.4f} trades={st} "
                    f"W/L/P={sw}/{sl}/{sp} winrate={wr_s} (wins ÷ wins+losses)"
                )
            print(
                f"[{ts}] RESULT mode={payload.get('mode')} won={payload.get('won')} "
                f"push={payload.get('push')} "
                f"pnl={payload.get('pnl', '—')} balance={payload.get('balance', '—')} "
                f"id={payload.get('order_id')}{extra}",
                flush=True,
            )
        elif event_type == "no_candles":
            print(f"[{ts}] NO CANDLES adapter={payload.get('adapter')} (waiting…)", flush=True)
        elif event_type == "data_error":
            raw_code = str(payload.get("error_code", "data_error")).strip().lower()
            code = raw_code if re.fullmatch(r"[a-z0-9_]{1,60}", raw_code) else "unknown_error"
            # The runner supplies only fixed, allow-listed hints; never print raw exceptions.
            hint = str(payload.get("hint", "")).replace("\r", " ").replace("\n", " ")[:180]
            detail = f"; {hint}" if hint else ""
            print(f"[{ts}] DATA ERROR {payload.get('adapter')}: {code}{detail}", flush=True)
        elif event_type == "adapter_connect":
            ok = payload.get("ok")
            ad = payload.get("adapter")
            if ok:
                print(f"[{ts}] CONNECT OK {ad}", flush=True)
            else:
                code = str(payload.get("error_code", "connect_failed"))[:60]
                diagnostic = str(payload.get("diagnostic", "cause_unavailable"))[:80]
                print(f"[{ts}] CONNECT FAIL {ad}: {code}; cause={diagnostic}", flush=True)
        elif event_type == "balance_init":
            print(f"[{ts}] BALANCE {payload.get('adapter')} = {payload.get('balance')}", flush=True)
        else:
            short = {k: v for k, v in payload.items() if k != "raw_result"}
            print(f"[{ts}] {event_type} {short}", flush=True)

    def log(self, event_type: str, **payload: Any) -> None:
        if not self.console:
            return
        try:
            self._print_console(event_type, dict(payload))
        except Exception as e:
            print(f"[logger] console print error: {e}", file=sys.stderr, flush=True)
