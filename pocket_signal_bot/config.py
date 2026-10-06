from __future__ import annotations

from dataclasses import dataclass
import math
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        print(f"[config] WARNING: {name}={raw!r} is not a valid integer; using default {default}", file=sys.stderr)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        print(f"[config] WARNING: {name}={raw!r} is not a valid number; using default {default}", file=sys.stderr)
        return default


@dataclass
class BotConfig:
    symbol: str = os.getenv("PO_SYMBOL", "EURUSD_otc")
    timeframe_sec: int = _env_int("PO_TIMEFRAME_SEC", 60)
    expiry_sec: int = _env_int("PO_EXPIRY_SEC", 60)
    candle_count: int = _env_int("PO_CANDLE_COUNT", 300)

    # Strategy
    ema_fast: int = _env_int("PO_EMA_FAST", 20)
    ema_slow: int = _env_int("PO_EMA_SLOW", 50)
    rsi_period: int = _env_int("PO_RSI_PERIOD", 14)
    buy_rsi_min: float = _env_float("PO_BUY_RSI_MIN", 52.0)
    sell_rsi_max: float = _env_float("PO_SELL_RSI_MAX", 48.0)
    rsi_neutral_band: float = _env_float("PO_RSI_NEUTRAL_BAND", 1.5)
    min_ema_gap: float = _env_float("PO_MIN_EMA_GAP", 0.0)
    require_momentum_confirm: bool = _env_bool("PO_REQUIRE_MOMENTUM_CONFIRM", True)
    # >0: require |fast_ema - slow_ema| >= this to allow the vote-fallback CALL/PUT (reduces chop)
    min_abs_ema_diff: float = _env_float("PO_MIN_ABS_EMA_DIFF", 0.0)
    # If false, strategy will never use vote fallback (strict EMA/RSI triggers only).
    allow_fallback_vote: bool = _env_bool("PO_ALLOW_FALLBACK_VOTE", True)
    # Require this many recent EMA-diff samples to keep same sign before any directional signal.
    min_trend_streak: int = max(1, _env_int("PO_MIN_TREND_STREAK", 2))
    # Chop filter lookback and minimum range-percent over that window.
    chop_lookback: int = max(5, _env_int("PO_CHOP_LOOKBACK", 20))
    min_range_pct: float = max(0.0, _env_float("PO_MIN_RANGE_PCT", 0.05))
    # Require this many consecutive polls with the same CALL/PUT before placing (1 = no confirmation)
    signal_confirm_polls: int = max(1, _env_int("PO_SIGNAL_CONFIRM_POLLS", 2))
    # After raw signal flips CALL↔PUT, skip directional trades for this many seconds (0 = off)
    flip_cooldown_sec: int = max(0, _env_int("PO_FLIP_COOLDOWN_SEC", 0))

    # Risk controls
    trade_amount: float = _env_float("PO_TRADE_AMOUNT", 1.0)
    min_payout_pct: float = _env_float("PO_MIN_PAYOUT_PCT", 70.0)
    max_signal_age_ms: int = _env_int("PO_MAX_SIGNAL_AGE_MS", 1500)
    max_candle_age_sec: int = _env_int("PO_MAX_CANDLE_AGE_SEC", 0)
    max_consecutive_losses: int = _env_int("PO_MAX_CONSECUTIVE_LOSSES", 3)
    max_trades_per_day: int = _env_int("PO_MAX_TRADES_PER_DAY", 20)
    daily_loss_stop_pct: float = _env_float("PO_DAILY_LOSS_STOP_PCT", 2.0)

    # Runtime mode: paper | demo | live | signals (signals never place orders)
    mode: str = os.getenv("PO_MODE", "paper")
    po_live_confirmed: bool = _env_bool("PO_LIVE_CONFIRMED", False)
    signal_interval_sec: int = max(60, _env_int("PO_SIGNAL_INTERVAL_SEC", 360))
    signal_min_alignment: int = max(70, min(100, _env_int("PO_SIGNAL_MIN_ALIGNMENT", 70)))
    signal_timezone: str = os.getenv("PO_SIGNAL_TIMEZONE", "Africa/Lusaka").strip() or "Africa/Lusaka"
    signal_ingest_url: str = os.getenv("SIGNAL_INGEST_URL", "").strip()
    signal_ingest_token: str = os.getenv("SIGNAL_INGEST_TOKEN", "").strip()
    adapter_priority: str = os.getenv("PO_ADAPTER_PRIORITY", "api_then_browser")
    poll_seconds: int = _env_int("PO_POLL_SECONDS", 2)

    # Per-adapter connect timeout (seconds). SDK / Playwright can hang; this limits damage.
    connect_timeout_sec: float = _env_float("PO_CONNECT_TIMEOUT_SEC", 120.0)
    # Per-call API timeout (candles, orders). Without this, one stuck SDK call freezes the bot.
    data_timeout_sec: float = _env_float("PO_DATA_TIMEOUT_SEC", 90.0)
    # true = skip pocket-option SDK entirely; rely only on Playwright (use when API hangs)
    skip_api_connect: bool = _env_bool("PO_SKIP_API_CONNECT", False)

    # Browser cabinet URLs
    po_browser_url_demo: str = os.getenv(
        "PO_BROWSER_URL_DEMO",
        "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
    )
    po_browser_url_live: str = os.getenv(
        "PO_BROWSER_URL_LIVE",
        "https://pocketoption.com/en/cabinet/quick-high-low/",
    )

    # Credentials / integration
    po_session: str = os.getenv("PO_SESSION", "")
    po_uid: str = os.getenv("PO_UID", "")
    po_is_demo: bool = _env_bool("PO_IS_DEMO", True)
    po_region: str = os.getenv("PO_REGION", "DEMO")

    # Browser (Playwright) options
    po_headless: bool = _env_bool("PO_HEADLESS", True)
    po_price_selectors: str = os.getenv("PO_PRICE_SELECTOR", "")
    po_payout_selectors: str = os.getenv("PO_PAYOUT_SELECTOR", "")
    po_browser_startup_wait_sec: int = _env_int("PO_BROWSER_STARTUP_WAIT_SEC", 5)
    po_use_ws_quotes: bool = _env_bool("PO_USE_WS_QUOTES", True)
    po_console_log: bool = _env_bool("PO_CONSOLE_LOG", True)
    po_browser_overlay: bool = _env_bool("PO_BROWSER_OVERLAY", True)
    po_ws_debug: bool = _env_bool("PO_WS_DEBUG", False)

    # Trade history + charts (PNG)
    trade_data_path: str = os.getenv("PO_TRADE_DATA_PATH", "data/trading_history.json")
    risk_state_path: str = os.getenv("PO_RISK_STATE_PATH", "data/risk_state.json")
    charts_dir: str = os.getenv("PO_CHARTS_DIR", "data/charts")
    charts_auto: bool = _env_bool("PO_CHARTS_AUTO", True)
    experiment_label: str = os.getenv("PO_EXPERIMENT_LABEL", "").strip()

    @property
    def time_pair_label(self) -> str:
        from pocket_signal_bot.trade_store import format_time_pair

        if self.experiment_label:
            return self.experiment_label
        return format_time_pair(self.timeframe_sec, self.expiry_sec)

    @property
    def price_selector_list(self) -> list[str]:
        raw = (self.po_price_selectors or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.split("|") if p.strip()]

    @property
    def payout_selector_list(self) -> list[str]:
        raw = (self.po_payout_selectors or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.split("|") if p.strip()]

    @property
    def effective_mode(self) -> str:
        m = (self.mode or "paper").strip().lower()
        if m not in ("paper", "demo", "live", "signals"):
            print(
                f"[config] WARNING: PO_MODE={self.mode!r} is not valid (paper|demo|live|signals); running as paper.",
                file=sys.stderr,
            )
            return "paper"
        return m

    @property
    def requires_broker(self) -> bool:
        return self.effective_mode in ("demo", "live", "signals")

    @property
    def api_is_demo(self) -> bool:
        # Never let a stale PO_IS_DEMO=false turn demo mode into real-money orders.
        return self.effective_mode != "live"

    @property
    def browser_base_url(self) -> str:
        if self.effective_mode == "live":
            return self.po_browser_url_live
        return self.po_browser_url_demo


def validate_config(cfg: BotConfig) -> None:
    """Validate safety-critical settings before connecting to a broker."""
    if cfg.effective_mode == "live":
        if not cfg.po_live_confirmed:
            raise SystemExit("Refusing live start: PO_LIVE_CONFIRMED=true is required.")
        if not os.getenv("PO_REGION", "").strip() or cfg.po_region.strip().upper() == "DEMO":
            raise SystemExit("Refusing live start: set PO_REGION to the verified real-account region.")
        if not os.getenv("PO_TRADE_AMOUNT", "").strip():
            raise SystemExit("Refusing live start: set a fixed PO_TRADE_AMOUNT explicitly.")
        if cfg.skip_api_connect:
            raise SystemExit("Refusing live start: PO_SKIP_API_CONNECT=true is not allowed.")
    if cfg.requires_broker and (not cfg.po_session or not cfg.po_uid):
        raise SystemExit("Refusing broker start: PO_SESSION and PO_UID are required.")
    if cfg.requires_broker and not cfg.po_uid.strip().isdigit():
        raise SystemExit("Refusing broker start: PO_UID must be numeric.")
    if cfg.effective_mode == "signals":
        from urllib.parse import urlparse
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        parsed = urlparse(cfg.signal_ingest_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SystemExit("Refusing signal start: SIGNAL_INGEST_URL must be a valid HTTPS URL.")
        if len(cfg.signal_ingest_token) < 32:
            raise SystemExit("Refusing signal start: SIGNAL_INGEST_TOKEN must be at least 32 characters.")
        try:
            ZoneInfo(cfg.signal_timezone)
        except ZoneInfoNotFoundError:
            raise SystemExit("Refusing signal start: PO_SIGNAL_TIMEZONE must be a valid timezone.") from None
        if cfg.skip_api_connect:
            raise SystemExit("Refusing signal start: PO_SKIP_API_CONNECT=true prevents market data access.")
    if cfg.effective_mode in ("demo", "live"):
        if not math.isfinite(cfg.trade_amount) or cfg.trade_amount <= 0:
            raise SystemExit("Refusing broker start: PO_TRADE_AMOUNT must be positive and finite.")
        if not math.isfinite(cfg.min_payout_pct) or not (0 < cfg.min_payout_pct < 100):
            raise SystemExit("Refusing broker start: PO_MIN_PAYOUT_PCT must be between 0 and 100.")
        if cfg.max_consecutive_losses < 1 or cfg.max_trades_per_day < 1:
            raise SystemExit("Refusing broker start: loss and daily trade limits must be positive.")
        if not math.isfinite(cfg.daily_loss_stop_pct) or cfg.daily_loss_stop_pct <= 0:
            raise SystemExit("Refusing broker start: PO_DAILY_LOSS_STOP_PCT must be positive.")
