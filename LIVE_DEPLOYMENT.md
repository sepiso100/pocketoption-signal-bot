# Production deployment

`render.yaml` defines an always-on paid Render web service, durable disk, and `PO_MODE=live`. It does not auto-deploy code changes. Render access and account secrets are not stored in GitHub.

## Required Render values

Set the prompted values directly in Render's secret/environment settings:

- `PO_LIVE_CONFIRMED=true`
- `PO_REGION` — verified real-account SDK region; never `DEMO`
- `PO_SESSION` and numeric `PO_UID` — real account only
- `PO_TRADE_AMOUNT` — one fixed stake; no Martingale/recovery increase
- `SIGNAL_CONTROL_TOKEN` — random secret of at least 32 characters

Never send broker sessions or control tokens in Telegram/chat or commit them.

## Current live-trading blocker

The broker SDK used here is unofficial and does not provide verified payout data. The worker now fails closed: it will not submit orders when payout/balance/settlement is unverified, and it never falls back to browser clicks. Do not sell or advertise live execution until a verified payout adapter and end-to-end real-account checks are complete.

## Pass Keys link

After Render deploys and `/healthz` passes, set on the Pass Keys service:

- `SIGNAL_BOT_CONTROL_URL` — worker's HTTPS service URL
- `SIGNAL_BOT_CONTROL_TOKEN` — the same secret as the worker

Use `/signalbot status` in Pass Keys to check state. Controls do not change trading mode. One worker/account only; customer isolation and billing are not implemented.
