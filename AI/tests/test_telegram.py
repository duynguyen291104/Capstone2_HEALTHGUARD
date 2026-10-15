from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from fallguard.notifications.telegram import TelegramNotifier, format_fall_alert

TOKEN = "123456:super-secret-token"
TELEGRAM_ENV_KEYS = (
    "FALLGUARD_TELEGRAM_BOT_TOKEN",
    "FALLGUARD_TELEGRAM_CHAT_ID",
    "FALLGUARD_TELEGRAM_CHAT_IDS",
    "FALLGUARD_TELEGRAM_ENABLED",
    "FALLGUARD_TELEGRAM_TIMEOUT_SECONDS",
)


def clear_telegram_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in TELEGRAM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def telegram_response(
    status_code: int = 200,
    *,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
) -> httpx.Response:
    request = httpx.Request(method, "https://api.telegram.org/redacted")
    return httpx.Response(
        status_code,
        json=payload or {"ok": True, "result": {"message_id": 42}},
        request=request,
    )


def test_from_env_supports_multiple_chat_ids() -> None:
    notifier = TelegramNotifier.from_env(
        {
            "FALLGUARD_TELEGRAM_BOT_TOKEN": TOKEN,
            "FALLGUARD_TELEGRAM_CHAT_IDS": "1001, 1002;1003 1002",
            "FALLGUARD_TELEGRAM_ENABLED": "true",
        }
    )

    assert notifier.enabled is True
    assert notifier.chat_ids == ("1001", "1002", "1003")
    assert TOKEN not in repr(notifier)


def test_from_env_replaces_invalid_timeout() -> None:
    notifier = TelegramNotifier.from_env(
        {
            "FALLGUARD_TELEGRAM_BOT_TOKEN": TOKEN,
            "FALLGUARD_TELEGRAM_CHAT_ID": "1001",
            "FALLGUARD_TELEGRAM_TIMEOUT_SECONDS": "-2",
        }
    )

    assert notifier.timeout_seconds == 8.0


def test_from_env_loads_workspace_dotenv_when_mapping_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_telegram_environment(monkeypatch)
    dotenv_token = "111111:dotenv-test-token"
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                f"FALLGUARD_TELEGRAM_BOT_TOKEN={dotenv_token}",
                "FALLGUARD_TELEGRAM_CHAT_ID=dotenv-chat",
                "FALLGUARD_TELEGRAM_ENABLED=true",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    notifier = TelegramNotifier.from_env()

    assert notifier.enabled is True
    assert notifier.chat_ids == ("dotenv-chat",)
    assert dotenv_token not in repr(notifier)


def test_from_env_explicit_mapping_does_not_load_or_mutate_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_telegram_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "FALLGUARD_TELEGRAM_BOT_TOKEN=222222:must-not-be-loaded",
                "FALLGUARD_TELEGRAM_CHAT_ID=must-not-be-loaded",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    notifier = TelegramNotifier.from_env(
        {
            "FALLGUARD_TELEGRAM_BOT_TOKEN": "333333:explicit-test-token",
            "FALLGUARD_TELEGRAM_CHAT_ID": "explicit-chat",
        }
    )

    assert notifier.enabled is True
    assert notifier.chat_ids == ("explicit-chat",)
    assert all(key not in os.environ for key in TELEGRAM_ENV_KEYS)


def test_from_env_process_environment_overrides_workspace_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_telegram_environment(monkeypatch)
    dotenv_token = "444444:dotenv-lower-priority"
    shell_token = "555555:shell-higher-priority"
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                f"FALLGUARD_TELEGRAM_BOT_TOKEN={dotenv_token}",
                "FALLGUARD_TELEGRAM_CHAT_ID=dotenv-chat",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALLGUARD_TELEGRAM_BOT_TOKEN", shell_token)
    monkeypatch.setenv("FALLGUARD_TELEGRAM_CHAT_ID", "shell-chat")

    notifier = TelegramNotifier.from_env()

    assert notifier.enabled is True
    assert notifier.chat_ids == ("shell-chat",)
    assert notifier._bot_token == shell_token
    assert dotenv_token not in repr(notifier)
    assert shell_token not in repr(notifier)


def test_from_env_redacts_token_from_dotenv_load_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clear_telegram_environment(monkeypatch)
    leaked_token = "666666:token-from-parser-error"
    (tmp_path / ".env").write_text("# test file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fail_to_load_dotenv(*args: Any, **kwargs: Any) -> None:
        raise ValueError(f"Could not parse token {leaked_token}")

    monkeypatch.setattr("dotenv.load_dotenv", fail_to_load_dotenv)
    caplog.set_level("WARNING", logger="fallguard.notifications.telegram")

    notifier = TelegramNotifier.from_env()

    assert notifier.enabled is False
    assert leaked_token not in caplog.text


def test_disabled_notifier_does_not_make_http_request(monkeypatch: Any) -> None:
    def must_not_post(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("HTTP must not be called")

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", must_not_post)
    notifier = TelegramNotifier(TOKEN, "1001", enabled=False)

    result = notifier.notify({"event_id": "FALL-1"})

    assert result.enabled is False
    assert result.success is False
    assert result.deliveries == ()
    assert result.skipped_reason


def test_notify_sends_vietnamese_text_to_every_recipient(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return telegram_response()

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", fake_post)
    notifier = TelegramNotifier(TOKEN, ["1001", "-2002"])
    event = {
        "event_id": "FALL-2026-01",
        "camera_id": "CAM-PHONG-01",
        "person_id": "USER-001",
        "risk_level": "HIGH",
        "fall_probability": 0.982,
        "posture": "LYING",
        "inactive_seconds": 8.2,
        "timestamp_ms": 2_000,
    }

    result = notifier.notify(event)

    assert result.success is True
    assert result.sent_count == 2
    assert result.failed_count == 0
    assert [call["data"]["chat_id"] for call in calls] == ["1001", "-2002"]
    assert all(call["url"].endswith("/sendMessage") for call in calls)
    assert "PHÁT HIỆN TÉ NGÃ" in calls[0]["data"]["text"]
    assert "98.2%" in calls[0]["data"]["text"]
    assert "CAM-PHONG-01" in calls[0]["data"]["text"]


def test_notify_sends_jpeg_snapshot_with_caption(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured.update({"url": url, **kwargs})
        return telegram_response()

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", fake_post)
    snapshot = tmp_path / "snapshot.jpg"
    snapshot.write_bytes(b"\xff\xd8jpeg-test\xff\xd9")
    notifier = TelegramNotifier(TOKEN, "1001")

    result = notifier.notify(
        {
            "event_id": "FALL-PHOTO-01",
            "metadata": {
                "camera_id": "CAM-01",
                "smoothed_fall_probability": 0.91,
            },
        },
        snapshot_jpeg=snapshot,
    )

    assert result.success is True
    assert result.deliveries[0].method == "sendPhoto"
    assert captured["url"].endswith("/sendPhoto")
    assert captured["data"]["chat_id"] == "1001"
    assert "FALL-PHOTO-01" in captured["data"]["caption"]
    filename, contents, content_type = captured["files"]["photo"]
    assert filename == "fallguard-event.jpg"
    assert contents == snapshot.read_bytes()
    assert content_type == "image/jpeg"


def test_notify_falls_back_to_text_when_snapshot_cannot_be_read(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(url)
        return telegram_response()

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", fake_post)
    notifier = TelegramNotifier(TOKEN, "1001")

    result = notifier.notify(
        {"event_id": "FALL-MISSING-PHOTO"},
        snapshot_jpeg=tmp_path / "missing.jpg",
    )

    assert result.success is True
    assert calls[0].endswith("/sendMessage")


def test_api_failure_is_structured_and_token_is_redacted(monkeypatch: Any) -> None:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return telegram_response(
            401,
            payload={
                "ok": False,
                "description": f"Unauthorized at {url}",
            },
        )

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", fake_post)
    notifier = TelegramNotifier(TOKEN, "1001")

    result = notifier.send_text("test")

    assert result.success is False
    assert result.failed_count == 1
    assert result.deliveries[0].status_code == 401
    assert TOKEN not in (result.deliveries[0].error or "")
    assert "[REDACTED]" in (result.deliveries[0].error or "")


def test_http_error_and_empty_snapshot_do_not_raise_or_expose_token(
    monkeypatch: Any,
) -> None:
    def fail_post(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        raise httpx.ConnectError(f"failed: {url}", request=request)

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.post", fail_post)
    notifier = TelegramNotifier(TOKEN, "1001")

    connection_result = notifier.send_text("test")
    empty_photo_result = notifier.send_photo(b"", caption="test")

    assert connection_result.success is False
    assert TOKEN not in (connection_result.deliveries[0].error or "")
    assert empty_photo_result.success is False
    assert "rỗng" in (empty_photo_result.deliveries[0].error or "")
    assert TOKEN not in repr(connection_result)


def test_format_fall_alert_accepts_event_metadata_and_sanitises_lines() -> None:
    text = format_fall_alert(
        {
            "event_id": "FALL-1\nINJECT",
            "occurred_at": "23/07/2026 23:50:00",
            "metadata": {
                "camera_id": "CAM-01",
                "person_id": "USER-01",
                "risk_level": "HIGH",
                "fall_probability": 96.4,
                "inactive_seconds": 9,
            },
        }
    )

    assert "FALL-1 INJECT" in text
    assert "96.4%" in text
    assert "9.0 giây" in text
    assert "23/07/2026 23:50:00" in text


def test_discover_recent_chats_deduplicates_updates(monkeypatch: Any) -> None:
    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "chat": {
                                "id": 1001,
                                "type": "private",
                                "first_name": "An",
                                "username": "an_user",
                            }
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {
                            "chat": {
                                "id": 1001,
                                "type": "private",
                                "first_name": "An",
                                "username": "an_user",
                            }
                        },
                    },
                    {
                        "update_id": 3,
                        "message": {
                            "chat": {
                                "id": -2002,
                                "type": "group",
                                "title": "Gia đình",
                            }
                        },
                    },
                ],
            },
            request=request,
        )

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.get", fake_get)
    notifier = TelegramNotifier(TOKEN, None)

    chats = notifier.discover_recent_chats()

    assert [(chat.chat_id, chat.title) for chat in chats] == [
        ("1001", "An"),
        ("-2002", "Gia đình"),
    ]


def test_discover_recent_chats_redacts_api_error(monkeypatch: Any) -> None:
    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            401,
            json={"ok": False, "description": f"Unauthorized: {url}"},
            request=request,
        )

    monkeypatch.setattr("fallguard.notifications.telegram.httpx.get", fake_get)
    notifier = TelegramNotifier(TOKEN, None)

    with pytest.raises(RuntimeError) as caught:
        notifier.discover_recent_chats()

    assert TOKEN not in str(caught.value)
