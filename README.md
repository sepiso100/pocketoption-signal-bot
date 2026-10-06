# Hybrid Trading Bots (MT5 + PocketOption)

This workspace now contains two independent bot paths:

- `mt5_signal_bot.py`: MT5 EUR/USD signal bot
- `pocket_signal_bot/`: PocketOption hybrid signal bot (`paper` + `demo`)

## PocketOption Hybrid Signal Bot

The Render blueprint defaults to `PO_MODE=signals` (read-only; no orders). Existing manually created Render services may retain their old environment values, so verify `PO_MODE` in Render. Live is a separate, explicit opt-in and remains fail-closed; see [LIVE_DEPLOYMENT.md](LIVE_DEPLOYMENT.md).

The PocketOption bot is designed around:

- EMA + RSI signal engine (`CALL` / `PUT` / `NO_TRADE`)
- Risk controls (daily stops, max losses, max trades/day, min payout)
- Broker orders use the unofficial API only; browser order clicks are disabled.
- Browser adapter is limited to read-only quotes and diagnostics.
- Structured event logs with timing fields

### Files

- `pocket_signal_bot/config.py`
- `pocket_signal_bot/strategy.py`
- `pocket_signal_bot/risk.py`
- `pocket_signal_bot/paper_simulator.py`
- `pocket_signal_bot/adapters/api_adapter.py`
- `pocket_signal_bot/adapters/browser_adapter.py`
- `pocket_signal_bot/runner.py`
- `pocket_signal_bot/logger.py`

## Install

```bash
pip install -r requirements.txt
```

If using browser fallback, also install browser runtime:

```bash
python -m playwright install chromium
```

### Demo mode: fix “Could not read current price from UI”

1. Set in `.env`: `PO_HEADLESS=false` and `PO_BROWSER_STARTUP_WAIT_SEC=15` (or higher on slow PCs).
2. Run `python -m pocket_signal_bot.runner`. A Chromium window opens on the quick-trading URL.
3. **Log in** if you see a login page; wait until the **live quote** is visible.
4. In normal Chrome on the same page: **F12 → Elements → select tool → click the price number → right‑click element → Copy → Copy selector**.
5. Put that string in `.env` as `PO_PRICE_SELECTOR=...` (use `|` between multiple selectors to try in order).
6. Run the bot again. If API still fails, browser candles will work once the selector matches.

The bot no longer exits the whole process when browser price read fails once; it logs `data_error` and retries on the next poll.

If the chart price is drawn on a **canvas** (no numeric `<span>`), keep **`PO_USE_WS_QUOTES=true`** (default). The browser adapter listens to the page **WebSocket** (e.g. `update_quotes`) and extracts the last price for **`PO_SYMBOL`** without needing `PO_PRICE_SELECTOR`.

## Environment file (optional)

Copy the sample and edit:

```bash
copy .env.example .env
```

`pocket_signal_bot/config.py` loads `.env` automatically when `python-dotenv` is installed.

## PocketOption credentials (demo first)

Set environment variables before running (or put them in `.env`):

- `PO_SESSION` -> session token from PocketOption web auth flow
- `PO_UID` -> your account uid
- `PO_IS_DEMO=true`
- `PO_MODE=paper` | `demo` | `live` (live = real money; requires `PO_LIVE_CONFIRMED=true`)
- Optional symbol/time controls:
  - `PO_SYMBOL=EURUSD_otc`
  - `PO_TIMEFRAME_SEC=60`
  - `PO_EXPIRY_SEC=60`

## Run

Paper mode (safe):

```bash
set PO_MODE=paper
python -m pocket_signal_bot.runner
```

Demo mode (PocketOption demo account):

```bash
set PO_MODE=demo
set PO_SESSION=your_session
set PO_UID=your_uid
python -m pocket_signal_bot.runner
```

Live mode (real money — same code path as demo, but `api_is_demo=false` and real cabinet URL for browser):

```bash
set PO_MODE=live
set PO_LIVE_CONFIRMED=true
set PO_SESSION=your_real_session
set PO_UID=your_real_uid
set PO_IS_DEMO=false
python -m pocket_signal_bot.runner
```

The bot refuses to start `PO_MODE=live` unless `PO_LIVE_CONFIRMED=true`, an explicit real-account `PO_REGION`, and a fixed `PO_TRADE_AMOUNT` are set. Demo mode always forces the API into demo mode, regardless of `PO_IS_DEMO`.

Live mode never clicks browser trade buttons. Missing/unverified broker balance, payout, order ID, or settlement blocks further trading. An uncertain order is persisted as pending and requires manual broker reconciliation before restarting. Risk limits persist only when `PO_RISK_STATE_PATH` is on durable storage. The current unofficial API does not expose verified payout data, so live orders remain blocked until a trustworthy payout source is implemented and verified.

Event output goes to the terminal when `PO_CONSOLE_LOG=true` (no log files are written).

## Trade history & PNG charts

Each run stores trades in `data/trading_history.json`. After every settled trade (if `PO_CHARTS_AUTO=true`), PNG charts are written to `data/charts/`:

| File | Content |
|------|---------|
| `latest_pnl.png` | Cumulative PnL + per-trade bars (current session) |
| `latest_winrate.png` | Win rate over trade number |
| `compare_setups.png` | Compare **15/15**, **30/30**, **60/60**, **M5/M3** sessions |
| `dashboard.png` | All sessions’ PnL curves overlaid |

**A/B test workflow** — change Pocket Option + `.env` for each setup, run demo, stop bot (Ctrl+C), repeat:

```env
# Example: 15s candle + 15s bet
PO_TIMEFRAME_SEC=15
PO_EXPIRY_SEC=15
```

Regenerate charts anytime:

```bash
python -m pocket_signal_bot.charts
```

Optional: `PO_EXPERIMENT_LABEL=15/15` overrides the auto label used in comparison charts.

## Readiness checklist before live

- At least 200+ demo trades logged
- Positive expectancy with realistic payout conditions
- No recurring adapter disconnect loop
- Latency and failover behavior verified in logs
- Risk limits tested (daily stop, max losses, max trades)

## Important

No strategy guarantees profit. Treat this as research/automation infrastructure and validate on demo first.

## Private signal delivery (read-only mode)

The worker can generate formatted, evidence-based signal cards and submit them to Pass Keys for private delivery to active subscribers of one exact product. It does not send a signal when the setup is weak, data is stale, or required analysis is unavailable.

Set `PO_MODE=signals` to enable this path. This mode requires PocketOption session credentials only to read market candles; the order method explicitly refuses to place trades. Keep the account in demo mode. It does not require a stake or payout value.

Required worker settings:

- `PO_MODE=signals`
- `PO_SESSION` and numeric `PO_UID` for market data
- `SIGNAL_INGEST_URL=https://<pass-keys-host>/v1/signals`
- `SIGNAL_INGEST_TOKEN` — same random 32+ character secret as Pass Keys
- `PO_SIGNAL_INTERVAL_SEC=360` (six-minute default)
- `PO_SIGNAL_MIN_ALIGNMENT=70` (minimum 70/100 confluence)
- `PO_SIGNAL_TIMEZONE=Africa/Lusaka`

Pass Keys must also set `SIGNAL_INGEST_TOKEN` and `SIGNAL_SUBSCRIPTION_APP_NAME` to the exact product key used under Firebase `loginDetails`. It rechecks active linked subscriptions at send time. Each customer must start the Telegram bot before private delivery can work.

Signal cards show market bias, EMA state, RSI, structure breaks when OHLC is available, and a clearly labelled candle-pressure proxy. True order flow is not supplied by this feed. The alignment score measures indicator agreement—not a win probability. The service never recommends Martingale or recovery staking.

This path has not been deployed or verified against a live broker feed. Use demo data first; no strategy guarantees profit.

## Render control API

Set `SIGNAL_CONTROL_TOKEN` to a random value of at least 32 characters to enable authenticated controls. Render supplies `PORT`; locally, the API defaults to `8080`.

- `GET /healthz` — public generic liveness response (`{"status":"ok"}`).
- `GET /v1/control/status` — Bearer-token status; omits credentials and account data.
- `POST /v1/control` — Bearer-token JSON `{"action":"pause"}`, `resume`, or `stop`.

Pause blocks new orders; resume allows them again. Stop permanently blocks future orders for that process. Remote controls never change `PO_MODE` or enable live trading. Mode changes require a deployment restart, and `PO_MODE=live` still requires `PO_LIVE_CONFIRMED=true` at startup. Before selling this service, use a separate isolated Render worker per customer.
