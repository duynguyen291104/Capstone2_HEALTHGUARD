from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
_TOKEN_IN_URL = re.compile(r"(?<=/bot)[^/\s]+", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResult:
    """Outcome for one Telegram recipient."""

    chat_id: str
    method: str
    success: bool
    status_code: int | None = None
    message_id: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramNotificationResult:
    """Aggregate outcome for a Telegram notification."""

    enabled: bool
    deliveries: tuple[TelegramDeliveryResult, ...] = ()
    skipped_reason: str | None = None

    @property
    def success(self) -> bool:
        return bool(self.deliveries) and all(item.success for item in self.deliveries)

    @property
    def sent_count(self) -> int:
        return sum(item.success for item in self.deliveries)

    @property
    def failed_count(self) -> int:
        return sum(not item.success for item in self.deliveries)

    def __bool__(self) -> bool:
        return self.success


@dataclass(frozen=True, slots=True)
class TelegramChatInfo:
    """A recent chat discovered from Telegram updates."""

    chat_id: str
    chat_type: str
    title: str
    username: str | None = None


class TelegramNotifier:
    """Best-effort Telegram Bot API notifier.

    Bot credentials are intentionally private and excluded from ``repr`` and
    delivery errors. Network failures are returned as structured results so a
    notification can never terminate the inference pipeline.
    """

    def __init__(
        self,
        bot_token: str | None,
        chat_ids: str | Sequence[str] | None,
        *,
        enabled: bool | None = None,
        timeout_seconds: float = 8.0,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._bot_token = bot_token.strip() if bot_token else ""
        self.chat_ids = _normalise_chat_ids(chat_ids)
        self.timeout_seconds = float(timeout_seconds)
        self.api_base_url = api_base_url.rstrip("/")
        self._requested_enabled = (
            bool(self._bot_token and self.chat_ids) if enabled is None else bool(enabled)
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(enabled={self.enabled!r}, "
            f"chat_ids={self.chat_ids!r}, timeout_seconds={self.timeout_seconds!r})"
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> TelegramNotifier:
        """Create a notifier from FallGuard environment variables.

        ``FALLGUARD_TELEGRAM_CHAT_IDS`` accepts comma, semicolon, or whitespace
        separated IDs. ``FALLGUARD_TELEGRAM_CHAT_ID`` remains available for a
        single recipient.
        """

        if environ is None:
            _load_local_dotenv()
        values = os.environ if environ is None else environ
        token = values.get("FALLGUARD_TELEGRAM_BOT_TOKEN")
        chat_ids = values.get("FALLGUARD_TELEGRAM_CHAT_IDS") or values.get(
            "FALLGUARD_TELEGRAM_CHAT_ID"
        )
        enabled_value = values.get("FALLGUARD_TELEGRAM_ENABLED")
        enabled = _parse_optional_bool(enabled_value)
        configured_timeout = timeout_seconds
        if configured_timeout is None:
            raw_timeout = values.get("FALLGUARD_TELEGRAM_TIMEOUT_SECONDS", "8")
            try:
                configured_timeout = float(raw_timeout)
            except ValueError:
                configured_timeout = 8.0
        if not math.isfinite(configured_timeout) or configured_timeout <= 0:
            configured_timeout = 8.0
        return cls(
            token,
            chat_ids,
            enabled=enabled,
            timeout_seconds=configured_timeout,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._requested_enabled and self._bot_token and self.chat_ids)

    def notify(
        self,
        event: Mapping[str, Any],
        *,
        snapshot_jpeg: bytes | bytearray | memoryview | Path | None = None,
    ) -> TelegramNotificationResult:
        """Send a Vietnamese fall alert built from an event mapping."""

        caption = format_fall_alert(event)
        if snapshot_jpeg is None:
            return self.send_text(caption)
        try:
            jpeg_bytes = _read_jpeg(snapshot_jpeg)
        except (OSError, TypeError, ValueError) as error:
            LOGGER.warning(
                "Cannot read the local fall snapshot; sending a text-only Telegram alert: %s",
                _sanitise_error(error, self._bot_token),
            )
            return self.send_text(caption)
        return self.send_photo(jpeg_bytes, caption=caption)

    def send_text(self, text: str) -> TelegramNotificationResult:
        if not self.enabled:
            return self._disabled_result()

        message = str(text).strip()[:4096]
        deliveries = tuple(
            self._deliver(
                chat_id,
                "sendMessage",
                data={"chat_id": chat_id, "text": message},
            )
            for chat_id in self.chat_ids
        )
        return TelegramNotificationResult(enabled=True, deliveries=deliveries)

    def send_photo(
        self,
        snapshot_jpeg: bytes | bytearray | memoryview | Path,
        *,
        caption: str,
        filename: str = "fallguard-event.jpg",
    ) -> TelegramNotificationResult:
        if not self.enabled:
            return self._disabled_result()

        try:
            jpeg_bytes = _read_jpeg(snapshot_jpeg)
        except (OSError, TypeError, ValueError) as error:
            public_error = _sanitise_error(error, self._bot_token)
            deliveries = tuple(
                TelegramDeliveryResult(
                    chat_id=chat_id,
                    method="sendPhoto",
                    success=False,
                    error=public_error,
                )
                for chat_id in self.chat_ids
            )
            return TelegramNotificationResult(enabled=True, deliveries=deliveries)

        deliveries = tuple(
            self._deliver(
                chat_id,
                "sendPhoto",
                data={"chat_id": chat_id, "caption": str(caption).strip()[:1024]},
                files={"photo": (filename, jpeg_bytes, "image/jpeg")},
            )
            for chat_id in self.chat_ids
        )
        return TelegramNotificationResult(enabled=True, deliveries=deliveries)

    def discover_recent_chats(self) -> tuple[TelegramChatInfo, ...]:
        """Return unique chats that recently sent an update to this bot."""

        if not self._bot_token:
            raise ValueError(
                "Thiếu FALLGUARD_TELEGRAM_BOT_TOKEN; không thể tìm Chat ID."
            )
        endpoint = f"{self.api_base_url}/bot{self._bot_token}/getUpdates"
        try:
            response = httpx.get(
                endpoint,
                timeout=self.timeout_seconds,
                headers={"User-Agent": "FallGuard-AI/0.1"},
            )
            body = _response_json(response)
        except httpx.TimeoutException as error:
            raise RuntimeError(
                "Hết thời gian chờ khi lấy Telegram updates."
            ) from error
        except (httpx.HTTPError, OSError) as error:
            raise RuntimeError(
                "Không thể kết nối Telegram Bot API để tìm Chat ID."
            ) from error

        if not response.is_success or not body.get("ok", False):
            description = _sanitise_text(
                str(body.get("description") or f"HTTP {response.status_code}"),
                self._bot_token,
            )
            raise RuntimeError(f"Telegram không trả về updates: {description}")

        updates = body.get("result")
        if not isinstance(updates, Sequence) or isinstance(
            updates, (str, bytes, bytearray)
        ):
            return ()
        chats: dict[str, TelegramChatInfo] = {}
        for update in updates:
            if not isinstance(update, Mapping):
                continue
            chat = _chat_from_update(update)
            if chat is not None:
                chats[chat.chat_id] = chat
        return tuple(chats.values())

    def _disabled_result(self) -> TelegramNotificationResult:
        return TelegramNotificationResult(
            enabled=False,
            skipped_reason="Telegram chưa được bật hoặc chưa đủ cấu hình.",
        )

    def _deliver(
        self,
        chat_id: str,
        method: str,
        *,
        data: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> TelegramDeliveryResult:
        endpoint = f"{self.api_base_url}/bot{self._bot_token}/{method}"
        try:
            response = httpx.post(
                endpoint,
                data=dict(data),
                files=dict(files) if files else None,
                timeout=self.timeout_seconds,
                headers={"User-Agent": "FallGuard-AI/0.1"},
            )
            body = _response_json(response)
            if response.is_success and body.get("ok", True):
                result = body.get("result")
                message_id = result.get("message_id") if isinstance(result, Mapping) else None
                return TelegramDeliveryResult(
                    chat_id=chat_id,
                    method=method,
                    success=True,
                    status_code=response.status_code,
                    message_id=message_id if isinstance(message_id, int) else None,
                )

            description = body.get("description")
            error = (
                str(description)
                if description
                else f"Telegram trả về HTTP {response.status_code}."
            )
            error = _sanitise_text(error, self._bot_token)
            LOGGER.warning(
                "Telegram delivery failed for chat %s: HTTP %s",
                chat_id,
                response.status_code,
            )
            return TelegramDeliveryResult(
                chat_id=chat_id,
                method=method,
                success=False,
                status_code=response.status_code,
                error=error,
            )
        except httpx.TimeoutException:
            error = "Hết thời gian chờ phản hồi từ Telegram."
        except (httpx.HTTPError, OSError) as exception:
            error = _sanitise_error(exception, self._bot_token)

        LOGGER.warning("Telegram delivery failed for chat %s: %s", chat_id, error)
        return TelegramDeliveryResult(
            chat_id=chat_id,
            method=method,
            success=False,
            error=error,
        )


def format_fall_alert(event: Mapping[str, Any]) -> str:
    """Build a compact Vietnamese alert from a FallGuard event mapping."""

    metadata = event.get("metadata")
    details = metadata if isinstance(metadata, Mapping) else {}

    event_id = _event_value(event, details, "event_id", default="Không có")
    camera_id = _event_value(event, details, "camera_id", default="Không xác định")
    person_id = _event_value(event, details, "person_id", default="Không xác định")
    risk = _event_value(event, details, "risk_level", "risk", default="HIGH")
    posture = _event_value(event, details, "posture", "activity", default="LYING")
    probability = _event_value(
        event,
        details,
        "fall_probability",
        "smoothed_fall_probability",
    )
    inactive = _event_value(event, details, "inactive_seconds")

    lines = [
        "🚨 FALLGUARD – PHÁT HIỆN TÉ NGÃ",
        "",
        f"Thời gian: {_format_event_time(event)}",
        f"Camera: {_safe_field(camera_id)}",
        f"Người: {_safe_field(person_id)}",
        f"Mức nguy cơ: {_safe_field(risk)}",
    ]
    probability_text = _format_probability(probability)
    if probability_text is not None:
        lines.append(f"Xác suất té ngã: {probability_text}")
    lines.append(f"Tư thế: {_safe_field(posture)}")
    inactive_text = _format_seconds(inactive)
    if inactive_text is not None:
        lines.append(f"Thời gian bất động: {inactive_text}")
    lines.extend(
        [
            f"Mã sự kiện: {_safe_field(event_id)}",
            "",
            "Vui lòng kiểm tra người được giám sát ngay!",
        ]
    )
    return "\n".join(lines)


def _normalise_chat_ids(chat_ids: str | Sequence[str] | None) -> tuple[str, ...]:
    if chat_ids is None:
        return ()
    raw_items = re.split(r"[,;\s]+", chat_ids) if isinstance(chat_ids, str) else chat_ids
    result: list[str] = []
    for item in raw_items:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _load_local_dotenv() -> None:
    """Load workspace-local secrets without overriding the active shell."""

    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=dotenv_path, override=False)
    except (OSError, UnicodeError, ValueError):
        LOGGER.warning(
            "Could not load local Telegram configuration from .env; "
            "the parser error was hidden to protect secrets."
        )


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    normalised = value.strip().lower()
    if normalised in _TRUE_VALUES:
        return True
    if normalised in _FALSE_VALUES:
        return False
    return None


def _read_jpeg(source: bytes | bytearray | memoryview | Path) -> bytes:
    data = source.read_bytes() if isinstance(source, Path) else bytes(source)
    if not data:
        raise ValueError("Ảnh chụp JPEG đang rỗng.")
    return data


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, Mapping) else {}


def _chat_from_update(update: Mapping[str, Any]) -> TelegramChatInfo | None:
    candidate: Any = None
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ):
        value = update.get(key)
        if isinstance(value, Mapping):
            candidate = value.get("chat")
            if isinstance(candidate, Mapping):
                break
    callback = update.get("callback_query")
    if not isinstance(candidate, Mapping) and isinstance(callback, Mapping):
        message = callback.get("message")
        if isinstance(message, Mapping):
            candidate = message.get("chat")
    if not isinstance(candidate, Mapping) or candidate.get("id") is None:
        return None

    first_name = str(candidate.get("first_name") or "").strip()
    last_name = str(candidate.get("last_name") or "").strip()
    title = str(candidate.get("title") or "").strip()
    display_name = title or " ".join(
        part for part in (first_name, last_name) if part
    )
    return TelegramChatInfo(
        chat_id=str(candidate["id"]),
        chat_type=str(candidate.get("type") or "unknown"),
        title=display_name or "Không có tên",
        username=(
            str(candidate["username"]).strip()
            if candidate.get("username")
            else None
        ),
    )


def _sanitise_error(error: BaseException, token: str) -> str:
    if isinstance(error, httpx.HTTPError):
        return "Không thể kết nối đến Telegram Bot API."
    return _sanitise_text(str(error), token) or "Không thể gửi thông báo Telegram."


def _sanitise_text(value: str, token: str) -> str:
    result = value.replace(token, "[REDACTED]") if token else value
    return _TOKEN_IN_URL.sub("[REDACTED]", result)


def _event_value(
    event: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    for source in (event, metadata):
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return default


def _format_event_time(event: Mapping[str, Any]) -> str:
    for key in ("occurred_at", "timestamp_iso", "created_at"):
        value = event.get(key)
        if value:
            return _safe_field(value)

    timestamp = event.get("timestamp_ms")
    try:
        timestamp_ms = float(timestamp)
    except (TypeError, ValueError):
        return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")
    if timestamp_ms >= 100_000_000_000:
        return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().strftime(
            "%d/%m/%Y %H:%M:%S"
        )
    return f"{timestamp_ms / 1000:.2f} giây (trong video)"


def _format_probability(value: Any) -> str | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if probability <= 1:
        probability *= 100
    return f"{probability:.1f}%"


def _format_seconds(value: Any) -> str | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return f"{seconds:.1f} giây"


def _safe_field(value: Any) -> str:
    return str(getattr(value, "value", value)).replace("\r", " ").replace("\n", " ").strip()
