# Pocket Option: safe Render checks

Keep `PO_MODE=signals`, `PO_REGION=DEMO`, and `PO_SKIP_API_CONNECT=false`.
Never print or paste `PO_SESSION`, `PO_UID`, tokens, cookies, or WebSocket frames.

In the Render Shell, check configuration without exposing secrets:

```sh
python - <<'PY'
import os
session = os.getenv("PO_SESSION", "")
uid = os.getenv("PO_UID", "").strip()
token = os.getenv("SIGNAL_INGEST_TOKEN", "")
print("PO_SESSION:", "set" if session.strip() else "MISSING")
print("PO_UID:", "numeric" if uid.isdigit() else "MISSING_OR_INVALID")
print("SIGNAL_INGEST_TOKEN:", "valid-length" if len(token) >= 32 else "MISSING_OR_SHORT")
print("PO_MODE:", os.getenv("PO_MODE", "<unset>"))
print("PO_REGION:", os.getenv("PO_REGION", "<unset>"))
print("PO_SKIP_API_CONNECT:", os.getenv("PO_SKIP_API_CONNECT", "<unset>"))
print("RENDER_GIT_COMMIT:", os.getenv("RENDER_GIT_COMMIT", "unknown")[:12])
PY
```

After deploying, check the `startup` log's `source_sha` against the Deploys page.
For API auth, look for `auth_response event=auth/success status=success` or
`event=successauth status=success`, followed by `wait_authorization status=ok`.
For data health, `no_candles` means there is still no usable feed; a `signal`
event means the runner accepted candle data. Signal mode never submits orders.

The pinned SDK 0.4.0 model serializes `session`; the observed browser frame uses
`sessionToken`. The adapter keeps `sessionToken` as the default. For one
controlled demo test, set `PO_AUTH_SESSION_FIELD=session`; it logs only the
selected field name. Restore the default if auth still times out. Never log or
share auth values or frames.
`updateAssets` and an open socket do not prove authorization.
