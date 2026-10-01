from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

from app.config import get_settings


@dataclass
class TelegramResult:
    delivered: bool
    message_id: str | None = None
    error: str | None = None


def is_public_action_url(value: str) -> bool:
    """Telegram rejects inline keyboard URLs that point to a local machine."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


class TelegramClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_message(
        self,
        *,
        chat_id: str | None,
        text: str,
        action_url: str | None = None,
        action_label: str = "Mở HealthGuard",
    ) -> TelegramResult:
        if not self.settings.telegram_bot_token:
            return TelegramResult(False, error="TELEGRAM_BOT_TOKEN is not configured")
        if not chat_id:
            return TelegramResult(False, error="Recipient has not linked Telegram")
        payload: dict = {"chat_id": chat_id, "text": text}
        if action_url and is_public_action_url(action_url):
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": action_label, "url": action_url}]]
            }
        endpoint = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(endpoint, json=payload)
            data = response.json()
            if response.is_success and data.get("ok"):
                return TelegramResult(True, message_id=str(data["result"]["message_id"]))
            description = str(data.get("description", "Telegram rejected the message"))[:500]
            return TelegramResult(False, error=description)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return TelegramResult(False, error=str(exc)[:500])


telegram_client = TelegramClient()
