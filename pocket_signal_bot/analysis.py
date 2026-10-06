from __future__ import annotations

import math
from typing import Any

from pocket_signal_bot.strategy import ema, rsi

WEIGHTS = {"ema": 0.30, "rsi": 0.20, "momentum": 0.20, "structure": 0.15, "candle_pressure": 0.15}


def analyze_market(
    candles: list[dict[str, Any]],
    candidate: str,
    *,
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    swing_lookback: int = 10,
) -> dict[str, Any]:
    """Build explainable confluence from available candle data; never invent OHLC/flow."""
    rows: list[dict[str, float]] = []
    for row in candles:
        try:
            close = float(row["close"])
            if not math.isfinite(close):
                continue
            item: dict[str, float] = {"close": close}
            values: dict[str, float] = {}
            for key in ("open", "high", "low"):
                raw = row.get(key)
                if raw is not None:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError("non-finite OHLC")
                    values[key] = value
            if all(k in values for k in ("open", "high", "low")):
                if values["high"] < max(values["open"], close) or values["low"] > min(values["open"], close):
                    continue
                item.update(values)
            rows.append(item)
        except (KeyError, TypeError, ValueError):
            continue

    closes = [row["close"] for row in rows]
    if len(closes) < 3:
        return _empty(candidate)

    fast = ema(closes, max(2, min(ema_fast, len(closes) - 1)))
    slow = ema(closes, max(2, min(ema_slow, len(closes) - 1)))
    rp = max(2, min(rsi_period, len(closes) - 1))
    rsi_values = rsi(closes, rp)
    fast_now, slow_now = fast[-1], slow[-1]
    fast_prev, slow_prev = fast[-2], slow[-2]
    rsi_now = rsi_values[-1]

    ema_side = 1 if fast_now is not None and slow_now is not None and fast_now > slow_now else (
        -1 if fast_now is not None and slow_now is not None and fast_now < slow_now else 0
    )
    ema_cross = "Bullish" if ema_side > 0 else "Bearish" if ema_side < 0 else "Neutral"
    if fast_prev is not None and slow_prev is not None and fast_now is not None and slow_now is not None:
        if fast_prev <= slow_prev and fast_now > slow_now:
            ema_cross = "Bullish crossover"
        elif fast_prev >= slow_prev and fast_now < slow_now:
            ema_cross = "Bearish crossover"

    rsi_num = float(rsi_now) if rsi_now is not None else None
    rsi_side = 1 if rsi_num is not None and rsi_num >= 55 else (-1 if rsi_num is not None and rsi_num <= 45 else 0)
    rsi_label = "Unavailable" if rsi_num is None else (
        "Overbought" if rsi_num >= 70 else "Oversold" if rsi_num <= 30 else
        "Bullish momentum" if rsi_num >= 55 else "Bearish momentum" if rsi_num <= 45 else "Neutral"
    )
    momentum_value = closes[-1] - closes[-3]
    momentum_side = 1 if momentum_value > 0 else (-1 if momentum_value < 0 else 0)

    structure_side = 0
    structure = "Unavailable (OHLC missing)"
    if len(rows) > swing_lookback and all("high" in x and "low" in x for x in rows[-(swing_lookback + 1):]):
        previous = rows[-(swing_lookback + 1):-1]
        if rows[-1]["close"] > max(x["high"] for x in previous):
            structure_side, structure = 1, "Bullish break of structure"
        elif rows[-1]["close"] < min(x["low"] for x in previous):
            structure_side, structure = -1, "Bearish break of structure"
        else:
            structure = "No confirmed break"

    pressure_side = 0
    pressure = "Unavailable (OHLC missing)"
    if len(rows) >= 5 and all("open" in x for x in rows[-5:]):
        bodies = [x["close"] - x["open"] for x in rows[-5:]]
        net = sum(bodies)
        scale = sum(abs(x) for x in bodies)
        ratio = net / scale if scale else 0.0
        if ratio >= 0.2:
            pressure_side, pressure = 1, "Buying-leaning candle proxy"
        elif ratio <= -0.2:
            pressure_side, pressure = -1, "Selling-leaning candle proxy"
        else:
            pressure = "Balanced candle proxy"

    bias = "Bullish" if ema_side > 0 else "Bearish" if ema_side < 0 else "Mixed"
    sides = {
        "ema": ema_side,
        "rsi": rsi_side,
        "momentum": momentum_side,
        "structure": structure_side,
        "candle_pressure": pressure_side,
    }
    available = [
        key for key, side in sides.items()
        if key == "ema" or side != 0 or _metric_available(key, rsi_num, rows, structure, pressure)
    ]
    direction = _direction(candidate)
    aligned = [key for key in available if direction != 0 and sides[key] == direction]
    denominator = sum(WEIGHTS[key] for key in available)
    score = round(100 * sum(WEIGHTS[key] for key in aligned) / denominator) if denominator else 0
    enough_evidence = len(available) >= 3 and len(aligned) >= 2
    signal = candidate if candidate in ("CALL", "PUT") and enough_evidence and score >= 70 else "NO_TRADE"

    return {
        "signal": signal,
        "market_bias": bias,
        "ema_state": ema_cross,
        "rsi_value": round(rsi_num, 1) if rsi_num is not None else None,
        "rsi_label": rsi_label,
        "structure": structure,
        "candle_pressure": pressure,
        "flow_note": "True order flow/market depth is not supplied by this feed.",
        "alignment_score": score,
        "aligned_factors": len(aligned),
        "available_factors": len(available),
        "score_note": "Confluence alignment, not win probability.",
        "momentum": momentum_value,
    }


def _direction(candidate: str) -> int:
    return 1 if candidate == "CALL" else -1 if candidate == "PUT" else 0


def _metric_available(key: str, rsi_num: float | None, rows: list[dict[str, float]], structure: str, pressure: str) -> bool:
    if key == "rsi":
        return rsi_num is not None
    if key == "momentum":
        return len(rows) >= 3
    if key == "structure":
        return not structure.startswith("Unavailable")
    if key == "candle_pressure":
        return not pressure.startswith("Unavailable")
    return True


def _empty(candidate: str) -> dict[str, Any]:
    return {
        "signal": "NO_TRADE",
        "market_bias": "Unavailable",
        "ema_state": "Unavailable",
        "rsi_value": None,
        "rsi_label": "Unavailable",
        "structure": "Unavailable (insufficient data)",
        "candle_pressure": "Unavailable (insufficient data)",
        "flow_note": "True order flow/market depth is not supplied by this feed.",
        "alignment_score": 0,
        "aligned_factors": 0,
        "available_factors": 0,
        "score_note": "Confluence alignment, not win probability.",
        "momentum": 0.0,
    }


def format_signal_message(
    *,
    symbol: str,
    side: str,
    entry_time: str,
    expiry_seconds: int,
    analysis: dict[str, Any],
) -> str:
    expiry = f"{expiry_seconds // 60} min" if expiry_seconds % 60 == 0 else f"{expiry_seconds} sec"
    order = "BUY / CALL" if side == "CALL" else "SELL / PUT"
    rsi_value = analysis.get("rsi_value")
    rsi_text = "Unavailable" if rsi_value is None else f"{rsi_value} — {analysis.get('rsi_label', 'Unclassified')}"
    return "\n".join([
        f"📡 POCKETOPTION SIGNAL — {symbol}",
        f"🕒 Entry: {entry_time}",
        f"⏱ Expiry: {expiry}",
        f"📈 Direction: {order}",
        "\n🔎 MARKET ANALYSIS",
        f"Bias: {analysis.get('market_bias', 'Unavailable')}",
        f"EMA: {analysis.get('ema_state', 'Unavailable')}",
        f"RSI: {rsi_text}",
        f"Structure: {analysis.get('structure', 'Unavailable')}",
        f"Candle pressure (proxy): {analysis.get('candle_pressure', 'Unavailable')}",
        "Order flow: unavailable from this feed (no real depth data).",
        f"\n🧭 Setup alignment: {analysis.get('alignment_score', 0)}/100 "
        f"({analysis.get('aligned_factors', 0)}/{analysis.get('available_factors', 0)} factors)",
        "Alignment is not a win probability. No signal means no trade.",
        "Use your own fixed risk limit; no Martingale/recovery staking.",
    ])
