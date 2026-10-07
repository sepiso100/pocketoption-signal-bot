# Signal delivery repair — 7 October 2026

## Confirmed code defects
- The old candle adapter probed client methods (`get_candles`, `history`, etc.) absent from the project's actual event-driven SDK.
- A socket connection plus a one-second sleep was treated as authenticated without waiting for broker confirmation.
- The control API implemented Stop but no Start; the admin also formatted valid `signals` mode as `unknown`.
- Browser fallback could reuse expired cached quotes as fresh and conflate OTC with regular-market symbols.
- Socket reconnect reset retry timing before any candle fetch succeeded.

## Changes
Pin `pocket-option==0.4.0` on Python 3.13+. Authenticate with broker acknowledgement. Subscribe through `client.emit`, request `load_history_period`, and consume matching `load_history_period_fast` OHLC responses. Reject invalid candles; never synthesize a successful feed or signal. Keep reconnect backoff until candle retrieval succeeds; distinguish data timeouts and recycle timed-out browser connections.

Add authenticated, signals-only Start. Preserve risk/pending-order holds and terminal Stop in trading modes. Deploy the paired Pass-Keys-Bot admin change for state-aware buttons.

## Deployment and live acceptance
1. Review/merge both repair pull requests. Deploy the worker first, then the admin. The repository's Render blueprint sets `autoDeploy: false`; merging alone is not deployment.
2. Verify `PO_MODE=signals`, broker session/UID/region, shared control/ingest tokens, admin ingest endpoint, channel target, and Telegram posting permissions using the deployment's secure settings. Do not post secret values in chat.
3. Verify broker authentication and fresh matching-symbol OHLC data. A CONNECT log alone is insufficient.
4. Exercise Stop → Status → Start; verify buttons and resumed candle processing.
5. Confirm a genuine strategy-qualified signal reaches the configured channel. Any diagnostic message must be clearly labeled as a test, never a trading signal.

Offline tests validate code behavior, not current credentials, broker availability, production deployment, or Telegram delivery. No live trading was enabled or orders placed.
