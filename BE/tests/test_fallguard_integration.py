from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app
from app.models import User
from app.routers import fallguard
from app.services.telegram import TelegramResult, telegram_client
from conftest import register_test_account


async def household(client):
    owner = await client.post("/api/v1/auth/register", json={
        "full_name": "Test owner", "email": "fall-owner@example.com", "password": "test-password",
    })
    assert owner.status_code == 201
    assert (await client.post("/api/v1/care-groups", json={"name": "Fall testing"})).status_code == 201
    elder = await client.post("/api/v1/elders", json={
        "full_name": "Test elder", "emergency_contact_name": "Test contact", "emergency_contact_phone": "0900000000",
    })
    assert elder.status_code == 201
    return owner.json()["user"]["id"], elder.json()["id"]


async def test_camera_alert_routes_only_to_linked_owner(client, db_factory, monkeypatch):
    import uuid

    owner_id, elder_id = await household(client)
    caregiver_id = await register_test_account("fall-caregiver@example.com")
    outsider_id = await register_test_account("fall-outsider@example.com")
    invitation = await client.post("/api/v1/care-groups/current/invitations", json={"email": "fall-caregiver@example.com"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as caregiver:
        await caregiver.post("/api/v1/auth/login", json={"email": "fall-caregiver@example.com", "password": "caregiver-password"})
        assert (await caregiver.post(f"/api/v1/invitations/{invitation.json()['invitation_token']}/accept")).status_code == 200
        assert (await caregiver.get(f"/api/v1/elders/{elder_id}/fallguard/events")).status_code == 404
        assert (await client.put(f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}", json={})).status_code == 200
        async with db_factory() as db:
            for user_id, chat in [(owner_id, "101"), (caregiver_id, "102"), (outsider_id, "103")]:
                (await db.get(User, uuid.UUID(user_id))).telegram_chat_id = chat
            await db.commit()
        key = "fake-service-key-for-automated-testing"
        monkeypatch.setattr(get_settings(), "fallguard_service_key", key)
        monkeypatch.setattr(telegram_client.settings, "telegram_bot_token", "fake-test-only")
        sent = []
        async def send_message(**kwargs):
            sent.append(kwargs)
            return TelegramResult(True)
        monkeypatch.setattr(telegram_client, "send_message", send_message)
        payload = {"elder_id": elder_id, "user_id": owner_id, "event_id": "FALL-0000000000010-ABCD1234", "notify": True}
        path = "/api/v1/fallguard/internal/events"
        assert (await client.post(path, json=payload)).status_code == 403
        headers = {"X-FallGuard-Key": key}
        assert (await client.post(path, json={**payload, "user_id": outsider_id}, headers=headers)).status_code == 404
        assert (await client.post(path, json=payload, headers=headers)).json() == {"telegram_status": "sent"}
        assert (await client.post(path, json=payload, headers=headers)).json() == {"telegram_status": "sent"}
        assert [msg["chat_id"] for msg in sent] == ["101"]
        assert all("TÉ NGÃ" in msg["text"] for msg in sent)
        history = await caregiver.get(f"/api/v1/elders/{elder_id}/fallguard/events")
        assert history.status_code == 200
        assert len(history.json()) == 1 and history.json()[0]["sent"] == 1
        async with db_factory() as db:
            (await db.get(User, uuid.UUID(owner_id))).telegram_chat_id = None
            await db.commit()
        assert (await client.post(path, json={**payload, "event_id": "FALL-0000000000020-ABCD1234"}, headers=headers)).json() == {"telegram_status": "no_recipients"}
        assert [msg["chat_id"] for msg in sent] == ["101"]


async def test_video_urfd_never_sends_and_result_bound_to_user_and_elder(client, monkeypatch):
    _, elder_id = await household(client)
    monkeypatch.setattr(get_settings(), "fallguard_service_key", "fake-service-key-for-automated-testing")
    async def analyze(method, path, **kwargs):
        assert method == "POST" and path == "/video"
        assert kwargs["params"] == {"suffix": ".mp4", "profile": "urfd"}
        assert b"".join([chunk async for chunk in kwargs["content"]]) == b"test"
        return {"event_ids": ["FALL-0000000000010-ABCD1234"], "result_id": "01f20937-3129-450b-bbab-17e12cce4816"}
    monkeypatch.setattr(fallguard, "call_ai", analyze)
    path = f"/api/v1/elders/{elder_id}/fallguard/video"
    reply = await client.post(path + "?suffix=.mp4&profile=urfd&notify=true", content=b"test")
    assert reply.status_code == 200, reply.text
    history = await client.get(f"/api/v1/elders/{elder_id}/fallguard/events")
    assert history.json()[0]["telegram_status"] == "suppressed"
    valid_path = reply.json()["video_path"]
    bad_path = valid_path[:-1] + ("0" if valid_path[-1] != "0" else "1")
    assert (await client.get("/api/v1" + bad_path)).status_code == 404
    other = await client.post("/api/v1/elders", json={
        "full_name": "Other elder", "emergency_contact_name": "Contact", "emergency_contact_phone": "0900000000",
    })
    assert (await client.get("/api/v1" + valid_path.replace(elder_id, other.json()["id"]))).status_code == 404
    await client.post("/api/v1/auth/logout")
    assert (await client.get("/api/v1" + valid_path)).status_code == 401


async def test_camera_requires_local_device_code(client, monkeypatch):
    _, elder_id = await household(client)
    monkeypatch.setattr(get_settings(), "fallguard_service_key", "fake-service-key-for-automated-testing")
    monkeypatch.setattr(get_settings(), "fallguard_camera_code", "fictional-device-code")
    calls = []
    async def start(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"session_id": "test"}
    monkeypatch.setattr(fallguard, "call_ai", start)
    path = f"/api/v1/elders/{elder_id}/fallguard/camera"
    assert (await client.post(path, json={"device_code": "wrong-code"})).status_code == 403
    assert not calls
    assert (await client.post(path, json={"device_code": "fictional-device-code"})).status_code == 200
    assert "device_code" not in calls[0][2]["json"]
