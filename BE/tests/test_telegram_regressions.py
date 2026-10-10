"""Telegram security checks: no real provider call is possible in these tests."""
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import User
from app.routers import integrations
from app.services import telegram


class StubHttpClient:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, *_args, **_kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("value", [
    "http://localhost./hom-nay", "http://computer/hom-nay", "http://family.local/hom-nay",
    "http://[broken", "https://public.example:invalid/", "https://user:password@public.example/",
])
def test_malformed_and_local_action_urls_do_not_crash_or_leak_credentials(value):
    assert not telegram.is_public_action_url(value)


@pytest.mark.parametrize("response", [
    httpx.Response(502, text="not JSON"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"ok": True, "result": None}),
])
async def test_malformed_provider_response_is_a_delivery_failure(monkeypatch, response):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", "FAKE_TOKEN_FOR_TEST_ONLY")
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **_: StubHttpClient(response))
    result = await telegram.TelegramClient().send_message(chat_id="111", text="Fictional unit test")
    assert not result.delivered
    assert "FAKE_TOKEN_FOR_TEST_ONLY" not in result.error


async def test_provider_errors_never_store_token_url(monkeypatch):
    settings = get_settings()
    fake_token = "FAKE_TOKEN_FOR_TEST_ONLY"
    monkeypatch.setattr(settings, "telegram_bot_token", fake_token)
    error = httpx.ConnectError(f"Unable to connect to https://api.telegram.org/bot{fake_token}/sendMessage")
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **_: StubHttpClient(error))
    result = await telegram.TelegramClient().send_message(chat_id="111", text="Fictional unit test")
    assert result.error == "Telegram request failed (ConnectError)"
    response = httpx.Response(400, json={"ok": False, "description": f"Rejected {fake_token}"})
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **_: StubHttpClient(response))
    result = await telegram.TelegramClient().send_message(chat_id="111", text="Fictional unit test")
    assert fake_token not in result.error
    assert "[redacted]" in result.error


async def test_webhook_only_links_private_chat_and_latest_unused_code(client, db_factory, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_username", "Fictional_Test_Bot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "FAKE_WEBHOOK_TEST_SECRET")
    sent = []
    async def fake_send(**kwargs):
        sent.append(kwargs)
        return telegram.TelegramResult(True, message_id="fake-webhook")
    monkeypatch.setattr(integrations.telegram_client, "send_message", fake_send)
    owner = await client.post("/api/v1/auth/register", json={
        "full_name": "Fictional Telegram owner", "email": "telegram-review@example.com",
        "password": "isolated-telegram-password",
    })
    assert owner.status_code == 201
    first = await client.post("/api/v1/auth/telegram-link")
    second = await client.post("/api/v1/auth/telegram-link")
    first_code = first.json()["deep_link"].split("?start=")[1]
    second_code = second.json()["deep_link"].split("?start=")[1]
    headers = {"X-Telegram-Bot-Api-Secret-Token": "FAKE_WEBHOOK_TEST_SECRET"}
    url = "/api/v1/integrations/telegram/webhook"
    def message(code, chat_type="private", chat_id=12345):
        return {"message": {"text": f"/start {code}", "chat": {"id": chat_id, "type": chat_type}, "from": {"id": 12345}}}
    rejected = await client.post(url, json=message(second_code))
    assert rejected.status_code == 401
    for body in [message(second_code, "group", -12345), {"message": "malformed"}, {"message": {"text": "x", "chat": None}}]:
        ignored = await client.post(url, headers=headers, json=body)
        assert ignored.status_code == 200 and ignored.json()["message"] == "ignored"
    assert sent == []
    async with db_factory() as db:
        user = await db.get(User, uuid.UUID(owner.json()["user"]["id"]))
        assert user.telegram_chat_id is None
    revoked = await client.post(url, headers=headers, json=message(first_code))
    assert revoked.json()["message"] == "expired code"
    empty = await client.post(url, headers=headers, json=message(""))
    assert empty.status_code == 200 and empty.json()["message"] == "invalid code"
    linked = await client.post(url, headers=headers, json=message(second_code))
    assert linked.json()["message"] == "linked"
    repeated = await client.post(url, headers=headers, json=message(second_code))
    assert repeated.json()["message"] == "expired code"
    async with db_factory() as db:
        user = await db.get(User, uuid.UUID(owner.json()["user"]["id"]))
        assert user.telegram_chat_id == "12345"
    await client.post("/api/v1/auth/logout")
    another = await client.post("/api/v1/auth/register", json={
        "full_name": "Second fictional owner", "email": "second-telegram@example.com",
        "password": "isolated-telegram-password",
    })
    another_link = await client.post("/api/v1/auth/telegram-link")
    another_code = another_link.json()["deep_link"].split("?start=")[1]
    duplicate_chat = await client.post(url, headers=headers, json=message(another_code))
    assert duplicate_chat.json()["message"] == "chat already linked"
    async with db_factory() as db:
        user = await db.get(User, uuid.UUID(another.json()["user"]["id"]))
        assert user.telegram_chat_id is None


async def test_inactive_account_cannot_link_telegram(client, db_factory, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_username", "Fictional_Test_Bot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "FAKE_WEBHOOK_TEST_SECRET")
    async def forbidden_send(**_):
        pytest.fail("Inactive account must not trigger any Telegram provider call")
    monkeypatch.setattr(integrations.telegram_client, "send_message", forbidden_send)
    owner = await client.post("/api/v1/auth/register", json={
        "full_name": "Disabled fictional user", "email": "disabled-telegram@example.com",
        "password": "isolated-telegram-password",
    })
    link = await client.post("/api/v1/auth/telegram-link")
    code = link.json()["deep_link"].split("?start=")[1]
    async with db_factory() as db:
        user = await db.get(User, uuid.UUID(owner.json()["user"]["id"]))
        user.is_active = False
        await db.commit()
    response = await client.post("/api/v1/integrations/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "FAKE_WEBHOOK_TEST_SECRET"}, json={"message": {"text": f"/start {code}", "chat": {"id": 45678, "type": "private"}, "from": {"id": 45678}}})
    assert response.status_code == 200 and response.json()["message"] == "account unavailable"
    async with db_factory() as db:
        user = await db.scalar(select(User).where(User.id == uuid.UUID(owner.json()["user"]["id"])))
        assert user.telegram_chat_id is None
