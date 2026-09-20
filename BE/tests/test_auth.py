from sqlalchemy import select

from app.models import User


OWNER = {
    "full_name": "Nguyen Van An",
    "email": "OWNER@example.com",
    "password": "very-strong-password",
    "care_group_name": "Gia dinh An",
}


async def test_register_login_me_and_logout_revokes_session(client, db_factory):
    response = await client.post("/api/v1/auth/register-owner", json=OWNER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["groups"][0]["role"] == "OWNER"
    assert "healthguard_access_token" in client.cookies

    async with db_factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        assert user is not None
        assert user.password_hash.startswith("$argon2")
        assert "very-strong-password" not in user.password_hash

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]

    duplicate = await client.post("/api/v1/auth/register-owner", json=OWNER)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    bad_login = await client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": "wrong"}
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["error"]["code"] == "INVALID_CREDENTIALS"

    good_login = await client.post(
        "/api/v1/auth/login",
        json={"email": OWNER["email"], "password": OWNER["password"]},
    )
    assert good_login.status_code == 200


async def test_owner_cannot_access_another_households_elder(client):
    first = await client.post("/api/v1/auth/register-owner", json=OWNER)
    first_group = first.json()["default_group_id"]
    elder = await client.post(
        "/api/v1/elders",
        headers={"X-Care-Group-ID": first_group},
        json={"full_name": "Cu Ba", "diagnosed_conditions": ["Tang huyet ap"]},
    )
    assert elder.status_code == 201
    elder_id = elder.json()["id"]
    await client.post("/api/v1/auth/logout")

    second = await client.post(
        "/api/v1/auth/register-owner",
        json={
            "full_name": "Owner B",
            "email": "owner-b@example.com",
            "password": "another-strong-password",
            "care_group_name": "Gia dinh B",
        },
    )
    second_group = second.json()["default_group_id"]
    forbidden = await client.get(
        f"/api/v1/elders/{elder_id}", headers={"X-Care-Group-ID": second_group}
    )
    assert forbidden.status_code == 404

