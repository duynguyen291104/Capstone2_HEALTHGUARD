from httpx import ASGITransport, AsyncClient

from app.main import app


ACCOUNT = {"full_name": "An", "email": "account@example.com", "password": "old-password-123"}


async def test_profile_is_persisted_and_cannot_change_privileges(client):
    assert (await client.patch("/api/v1/auth/me", json={"full_name": "New"})).status_code == 401
    await client.post("/api/v1/auth/register", json=ACCOUNT)
    response = await client.patch("/api/v1/auth/me", json={"full_name": "  An Nguyen  ", "phone": " 0901234567 "})
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "An Nguyen"
    assert (await client.get("/api/v1/auth/me")).json()["user"]["phone"] == "0901234567"
    assert (await client.patch("/api/v1/auth/me", json={"full_name": "  "})).status_code == 422
    assert (await client.patch("/api/v1/auth/me", json={"full_name": "An", "role": "OWNER"})).status_code == 422
    await client.patch("/api/v1/auth/me", json={"full_name": "An", "phone": ""})
    assert (await client.get("/api/v1/auth/me")).json()["user"]["phone"] is None


async def test_password_change_revokes_old_sessions_and_keeps_current(client):
    await client.post("/api/v1/auth/register", json=ACCOUNT)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        await other.post("/api/v1/auth/login", json={"email": ACCOUNT["email"], "password": ACCOUNT["password"]})
        wrong = await client.post("/api/v1/auth/change-password", json={"current_password": "wrong", "new_password": "new-password-123"})
        assert wrong.status_code == 400
        assert (await other.get("/api/v1/auth/me")).status_code == 200
        short = await client.post("/api/v1/auth/change-password", json={"current_password": ACCOUNT["password"], "new_password": "short"})
        assert short.status_code == 422
        same = await client.post("/api/v1/auth/change-password", json={"current_password": ACCOUNT["password"], "new_password": ACCOUNT["password"]})
        assert same.status_code == 400
        changed = await client.post("/api/v1/auth/change-password", json={"current_password": ACCOUNT["password"], "new_password": "new-password-123"})
        assert changed.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        assert (await other.get("/api/v1/auth/me")).status_code == 401
        assert (await other.post("/api/v1/auth/login", json={"email": ACCOUNT["email"], "password": ACCOUNT["password"]})).status_code == 401
        assert (await other.post("/api/v1/auth/login", json={"email": ACCOUNT["email"], "password": "new-password-123"})).status_code == 200
