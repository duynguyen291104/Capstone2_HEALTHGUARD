from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


class WebhookNotifier:
    """Best-effort JSON webhook; inference never fails when delivery fails."""

    def __init__(
        self,
        url: str | None,
        *,
        timeout_seconds: float = 5.0,
        secret: str | None = None,
    ) -> None:
        self.url = url.strip() if url else None
        self.timeout_seconds = float(timeout_seconds)
        self.secret = secret
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def notify(self, event: Mapping[str, Any]) -> bool:
        if not self.url:
            return False
        body = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FallGuard-AI/0.1",
        }
        if self.secret:
            signature = hmac.new(
                self.secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-FallGuard-Signature"] = f"sha256={signature}"
        try:
            response = httpx.post(
                self.url,
                content=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            self.last_error = None
            return True
        except (httpx.HTTPError, OSError) as error:
            self.last_error = str(error)
            LOGGER.warning("FallGuard webhook delivery failed: %s", error)
            return False
