# Render deployment and live opt-in

`render.yaml` defaults to `PO_MODE=signals`: read-only analysis with no order submission. It provisions Chromium for the browser data fallback and does not auto-deploy code changes. Existing manually created Render services may ignore blueprint defaults; check their environment settings directly.

## Signal service setup

Set these in the Render worker's protected environment:

- `PO_SESSION` and numeric `PO_UID` — authorized demo-account session and ID
- `PO_IS_DEMO=true`, `PO_REGION=DEMO`, `PO_MODE=signals`
- `PO_SKIP_API_CONNECT=false`
- `SIGNAL_INGEST_URL=https://<pass-keys-host>/v1/signals`
- `SIGNAL_INGEST_TOKEN` — random secret, at least 32 characters
- `SIGNAL_CONTROL_TOKEN` — separate random secret, at least 32 characters

Keep broker sessions and tokens out of GitHub, Telegram, and chat.

On Pass Keys, configure `SIGNAL_BOT_CONTROL_URL` as the worker's HTTPS root URL and `SIGNAL_BOT_CONTROL_TOKEN` as the exact worker `SIGNAL_CONTROL_TOKEN`. For signal delivery, configure `SIGNAL_INGEST_TOKEN` to match the worker's ingest token and set `SIGNAL_SUBSCRIPTION_APP_NAME` to the exact Firebase `loginDetails` product key. When System Hub central secrets are enabled, assign the Pass Keys tokens in its vault; otherwise use protected host secrets.

## Live trading is explicit opt-in

Do not change signal mode to live for this signal product. Live execution requires an intentional Render environment change to `PO_MODE=live`, `PO_LIVE_CONFIRMED=true`, a verified non-demo `PO_REGION`, real-account `PO_SESSION` and numeric `PO_UID`, `PO_IS_DEMO=false`, and an explicitly fixed `PO_TRADE_AMOUNT`. No live mode is enabled by this blueprint. The worker remains API-only for live orders and fails closed on unavailable API, unverified payout/balance, or uncertain settlement; it never falls back to browser order clicks. The unofficial API does not currently provide verified payout data, so live orders remain blocked pending a verified adapter and end-to-end checks.
