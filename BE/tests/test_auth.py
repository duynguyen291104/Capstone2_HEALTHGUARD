from sqlalchemy import select

from app.models import User


OWNER = {
    "full_name": "Nguyen Van An",
    "email": "OWNER@example.com",
    "password": "very-strong-password",
}


async def test_register_login_me_and_logout_revokes_session(client, db_factory):
    response = await client.post("/api/v1/auth/register", json=OWNER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["groups"] == []
    assert body["default_group_id"] is None
    assert "healthguard_access_token" in client.cookies

    group = await client.post("/api/v1/care-groups", json={"name": "Gia dinh An"})
    assert group.status_code == 201, group.text
    assert group.json()["role"] == "OWNER"
    second_group = await client.post("/api/v1/care-groups", json={"name": "Nhom khac"})
    assert second_group.status_code == 409
    assert second_group.json()["error"]["code"] == "GROUP_ALREADY_EXISTS"

    async with db_factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        assert user is not None
        assert user.password_hash.startswith("$argon2")
        assert "very-strong-password" not in user.password_hash

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]
    assert me.json()["groups"][0]["role"] == "OWNER"

    duplicate = await client.post("/api/v1/auth/register", json=OWNER)
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


async def test_registration_rejects_blank_names(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "   ",
            "email": "blank-name@example.com",
            "password": "very-strong-password",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_owner_cannot_access_another_households_elder(client):
    first = await client.post("/api/v1/auth/register", json=OWNER)
    assert first.status_code == 201
    first_group_response = await client.post(
        "/api/v1/care-groups", json={"name": "Gia dinh An"}
    )
    first_group = first_group_response.json()["id"]
    elder = await client.post(
        "/api/v1/elders",
        headers={"X-Care-Group-ID": first_group},
        json={"full_name": "Cu Ba", "diagnosed_conditions": ["Tang huyet ap"]},
    )
    assert elder.status_code == 201
    elder_id = elder.json()["id"]
    await client.post("/api/v1/auth/logout")

    second = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Owner B",
            "email": "owner-b@example.com",
            "password": "another-strong-password",
        },
    )
    assert second.status_code == 201
    second_group_response = await client.post(
        "/api/v1/care-groups", json={"name": "Gia dinh B"}
    )
    second_group = second_group_response.json()["id"]
    forbidden = await client.get(
        f"/api/v1/elders/{elder_id}", headers={"X-Care-Group-ID": second_group}
    )
    assert forbidden.status_code == 404


async def test_existing_account_without_group_can_accept_caregiver_invitation(client):
    owner = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Chu nha",
            "email": "invite-owner@example.com",
            "password": "very-strong-password",
        },
    )
    assert owner.status_code == 201
    group = await client.post("/api/v1/care-groups", json={"name": "Gia dinh moi"})
    group_id = group.json()["id"]
    invitation = await client.post(
        "/api/v1/care-groups/current/invitations",
        headers={"X-Care-Group-ID": group_id},
        json={"email": "existing-helper@example.com"},
    )
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()["invitation_token"]
    await client.post("/api/v1/auth/logout")

    helper = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Nguoi cham soc",
            "email": "existing-helper@example.com",
            "password": "caregiver-password",
        },
    )
    assert helper.status_code == 201
    assert helper.json()["groups"] == []
    accepted = await client.post(f"/api/v1/invitations/{token}/accept")
    assert accepted.status_code == 200, accepted.text
    me = await client.get("/api/v1/auth/me")
    assert me.json()["groups"][0]["role"] == "CAREGIVER"
