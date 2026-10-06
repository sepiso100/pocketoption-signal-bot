from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SignalPublisher:
    """Push validated signal cards to the authenticated Pass Keys delivery endpoint."""

    def __init__(self, url: str, token: str, timeout_sec: float = 10.0):
        self.url = (url or "").strip()
        self.token = (token or "").strip()
        self.timeout_sec = max(1.0, min(float(timeout_sec), 20.0))

    @property
    def configured(self) -> bool:
        parsed = urlparse(self.url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and bool(self.token)
            and len(self.token) >= 32
        )

    async def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "reason": "delivery_not_configured"}
        return await asyncio.to_thread(self._post, payload)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > 12_000:
            return {"ok": False, "reason": "payload_too_large"}
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PocketSignalWorker/1.0",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        last = {"ok": False, "reason": "delivery_unavailable"}
        for attempt in range(3):
            try:
                with opener.open(request, timeout=self.timeout_sec) as response:
                    if response.status != 200:
                        last = {"ok": False, "reason": f"http_{response.status}"}
                    else:
                        raw = response.read(4096)
                        result = json.loads(raw.decode("utf-8"))
                        if isinstance(result, dict) and result.get("ok") is True:
                            return {
                                "ok": True,
                                "sent": int(result.get("sent", 0)),
                                "failed": int(result.get("failed", 0)),
                                "skipped": int(result.get("skipped", 0)),
                            }
                        last = {"ok": False, "reason": "delivery_rejected"}
            except urllib.error.HTTPError as exc:
                last = {"ok": False, "reason": f"http_{exc.code}"}
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                last = {"ok": False, "reason": "delivery_unavailable"}
            retryable = last["reason"] == "delivery_unavailable" or last["reason"] in {
                "http_429", "http_500", "http_502", "http_503", "http_504",
            }
            if attempt == 2 or not retryable:
                break
            time.sleep(0.25 * (attempt + 1))
        return last
