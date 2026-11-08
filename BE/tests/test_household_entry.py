from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models import Invitation


async def register(client, name):
    result = await client.post("/api/v1/auth/register", json={
        "email": f"{name}@example.com", "full_name": name, "password": "test-long-password",
    })
    assert result.status_code == 201, result.text


async def test_room_code_admits_caregiver_without_elder_access(client):
    await register(client, "room-owner")
    await client.post("/api/v1/care-groups", json={"name": "Test household"})
    elder = await client.post("/api/v1/elders", json={
        "full_name": "Test elder", "emergency_contact_name": "Test contact", "emergency_contact_phone": "0900000000",
    })
    assert elder.status_code == 201
    issued = await client.post("/api/v1/care-groups/current/room-code", json={"password": "test-room-password"})
    assert issued.status_code == 200
    code = issued.json()["code"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as guest:
        assert (await guest.post("/api/v1/care-groups/join", json={"code": code})).status_code == 401
        await register(guest, "room-caregiver")
        assert (await guest.post("/api/v1/care-groups/join", json={"code": code, "password": "wrong-password"})).status_code == 403
        joined = await guest.post("/api/v1/care-groups/join", json={"code": code, "password": "test-room-password"})
        assert joined.status_code == 200 and joined.json()["role"] == "CAREGIVER"
        assert (await guest.get("/api/v1/elders")).json() == []
        assert (await guest.post("/api/v1/care-groups/current/room-code", json={})).status_code == 403
        assert (await guest.post("/api/v1/care-groups/join", json={"code": code})).status_code == 409
    replacement = await client.post("/api/v1/care-groups/current/room-code", json={})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as guest:
        await register(guest, "room-caregiver-two")
        assert (await guest.post("/api/v1/care-groups/join", json={"code": code})).status_code == 404
        assert (await guest.post("/api/v1/care-groups/join", json={"code": replacement.json()["code"]})).status_code == 200
    assert (await client.delete("/api/v1/care-groups/current/room-code")).status_code == 204
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as guest:
        await register(guest, "room-caregiver-three")
        assert (await guest.post("/api/v1/care-groups/join", json={"code": replacement.json()["code"]})).status_code == 404


async def test_inviting_unknown_email_does_not_create_invitation(client, db_factory):
    await register(client, "invite-owner")
    await client.post("/api/v1/care-groups", json={"name": "Test household"})
    result = await client.post("/api/v1/care-groups/current/invitations", json={"email": "missing@example.com"})
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "INVITED_ACCOUNT_NOT_FOUND"
    async with db_factory() as db:
        assert await db.scalar(select(Invitation.id)) is None


async def test_elder_requires_valid_emergency_contact(client):
    await register(client, "contact-owner")
    await client.post("/api/v1/care-groups", json={"name": "Test household"})
    path = "/api/v1/elders"
    valid = {"full_name": "Test elder", "emergency_contact_name": "Test contact", "emergency_contact_phone": "+84 900 000 000"}
    for field in ("emergency_contact_name", "emergency_contact_phone"):
        assert (await client.post(path, json={key: value for key, value in valid.items() if key != field})).status_code == 422
        assert (await client.post(path, json={**valid, field: "   "})).status_code == 422
    assert (await client.post(path, json={**valid, "emergency_contact_phone": "not-a-number"})).status_code == 422
    created = await client.post(path, json=valid)
    assert created.status_code == 201, created.text
    assert (await client.patch(f"{path}/{created.json()['id']}", json={"emergency_contact_phone": None})).status_code == 422
