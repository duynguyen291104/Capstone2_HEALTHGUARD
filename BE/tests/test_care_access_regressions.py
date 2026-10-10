"""Isolated account/invitation/permission regressions; no external services."""

import uuid
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.models import (
    AuthSession,
    CaregiverAssignment,
    DoseOccurrence,
    DoseStatus,
    Invitation,
    MedicationSchedule,
    NotificationAttempt,
    NotificationKind,
    TelegramLinkCode,
    User,
)


def account(email="owner-regression@example.com"):
    return {"full_name": "Chủ nhóm", "email": email, "password": "owner-safe-password"}


async def household(client):
    registered = await client.post("/api/v1/auth/register", json=account())
    assert registered.status_code == 201, registered.text
    group = await client.post("/api/v1/care-groups", json={"name": "Nhóm kiểm thử"})
    assert group.status_code == 201, group.text
    elder = await client.post("/api/v1/elders", json={
        "full_name": "Bà mẫu", "diagnosed_conditions": ["Thông tin chẩn đoán"],
        "current_medications_note": "Thông tin thuốc cần bảo vệ",
    })
    assert elder.status_code == 201, elder.text
    return registered.json()["user"]["id"], group.json()["id"], elder.json()["id"]


async def helper(owner, caregiver, email="helper-regression@example.com"):
    invitation = await owner.post("/api/v1/care-groups/current/invitations", json={"email": email})
    assert invitation.status_code == 201, invitation.text
    registered = await caregiver.post("/api/v1/auth/register-caregiver", json={
        "full_name": "Người chăm sóc", "email": email, "password": "helper-safe-password",
        "invitation_token": invitation.json()["invitation_token"],
    })
    assert registered.status_code == 201, registered.text
    return registered.json()["user"]["id"], invitation.json()


async def assign(owner, elder_id, caregiver_id, **permissions):
    response = await owner.put(
        f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}", json=permissions,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def schedule(owner, elder_id, caregiver_id=None):
    response = await owner.post(f"/api/v1/elders/{elder_id}/medication-schedules", json={
        "medication_name": "Thuốc mẫu", "dose_amount": "1", "dose_unit": "viên",
        "start_date": date.today().isoformat(), "time_of_day": "08:00",
        "assigned_caregiver_user_id": caregiver_id,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_logout_clears_missing_invalid_expired_and_revoked_cookie(client, db_factory):
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    await client.post("/api/v1/auth/register", json=account())
    cookie_name = get_settings().cookie_name
    token = client.cookies.get(cookie_name)
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert cookie_name not in client.cookies
    client.cookies.set(cookie_name, token, domain="testserver.local", path="/")
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    client.cookies.set(cookie_name, "malformed-browser-cookie", domain="testserver.local", path="/")
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert cookie_name not in client.cookies
    async with db_factory() as db:
        session = await db.scalar(select(AuthSession))
        assert session.revoked_at is not None
    await client.post("/api/v1/auth/login", json={
        "email": account()["email"], "password": account()["password"],
    })
    async with db_factory() as db:
        session = await db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert cookie_name not in client.cookies


async def test_signed_malformed_session_claims_are_401_and_account_data_not_cached(client):
    registered = await client.post("/api/v1/auth/register", json=account())
    me = await client.get("/api/v1/auth/me")
    assert me.headers["cache-control"] == "no-store"
    settings = get_settings()
    claims = {
        "sub": registered.json()["user"]["id"], "sid": str(uuid.uuid4()),
        "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    for invalid in ({**claims, "sid": []}, {key: value for key, value in claims.items() if key != "exp"}):
        client.cookies.clear()
        client.cookies.set(settings.cookie_name, jwt.encode(invalid, settings.jwt_secret, algorithm=settings.jwt_algorithm))
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_SESSION"


@pytest.mark.parametrize("caregiver_registration", [False, True])
async def test_registration_insert_conflict_returns_409_without_partial_user(client, db_factory, monkeypatch, caregiver_registration):
    target = "insert-conflict@example.com"
    payload = account(target)
    endpoint = "/api/v1/auth/register"
    if caregiver_registration:
        await household(client)
        invitation = await client.post("/api/v1/care-groups/current/invitations", json={"email": target})
        payload["invitation_token"] = invitation.json()["invitation_token"]
        endpoint = "/api/v1/auth/register-caregiver"
        await client.post("/api/v1/auth/logout")
    original = AsyncSession.flush
    async def conflict(self, *args, **kwargs):
        if any(isinstance(item, User) and item.email == target for item in self.new):
            raise IntegrityError("INSERT users", {}, Exception("concurrent unique email conflict"))
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(AsyncSession, "flush", conflict)
    response = await client.post(endpoint, json=payload)
    assert response.status_code == 409
    assert get_settings().cookie_name not in client.cookies
    async with db_factory() as db:
        assert await db.scalar(select(User.id).where(User.email == target)) is None
        if caregiver_registration:
            invitation = await db.scalar(select(Invitation).where(Invitation.email == target))
            assert invitation.accepted_at is None


async def test_invitation_reissue_expiry_revoke_and_single_use_lifecycle(client, db_factory):
    _, group_id, _ = await household(client)
    first = await client.post("/api/v1/care-groups/current/invitations", json={"email": "invited@example.com"})
    second = await client.post("/api/v1/care-groups/current/invitations", json={"email": "INVITED@example.com"})
    assert first.status_code == second.status_code == 201
    assert (await client.get(f"/api/v1/invitations/{first.json()['invitation_token']}")).status_code == 410
    public = await client.get(f"/api/v1/invitations/{second.json()['invitation_token']}")
    assert public.status_code == 200 and public.json()["email"] == "invited@example.com"
    listed = await client.get("/api/v1/care-groups/current/invitations")
    assert all("token_hash" not in row and "invitation_token" not in row for row in listed.json())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as caregiver:
        helper_account = account("invited@example.com")
        await caregiver.post("/api/v1/auth/register", json=helper_account)
        accepted = await caregiver.post(f"/api/v1/invitations/{second.json()['invitation_token']}/accept")
        assert accepted.status_code == 200
        assert (await caregiver.post(f"/api/v1/invitations/{second.json()['invitation_token']}/accept")).status_code == 409
        assert (await caregiver.get("/api/v1/care-groups/current/members")).status_code == 403
        assert (await caregiver.post("/api/v1/care-groups/current/invitations", json={"email": "other@example.com"})).status_code == 403
    assert (await client.delete(f"/api/v1/care-groups/current/invitations/{second.json()['id']}")).status_code == 409
    assert (await client.delete(f"/api/v1/care-groups/current/invitations/{first.json()['id']}")).status_code == 204
    expired = await client.post("/api/v1/care-groups/current/invitations", json={"email": "expired@example.com"})
    async with db_factory() as db:
        row = await db.get(Invitation, uuid.UUID(expired.json()["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        outstanding = await db.scalar(select(func.count()).select_from(Invitation).where(
            Invitation.group_id == uuid.UUID(group_id), Invitation.email == "invited@example.com",
            Invitation.revoked_at.is_(None), Invitation.accepted_at.is_(None),
        ))
        assert outstanding == 0
    assert (await client.get(f"/api/v1/invitations/{expired.json()['invitation_token']}")).status_code == 410


async def test_caregiver_permission_matrix_protects_diagnosis_and_medication_note(client, db_factory):
    _, group_id, elder_id = await household(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as caregiver:
        caregiver_id, _ = await helper(client, caregiver)
        await assign(client, elder_id, caregiver_id, can_view_medications=False, can_confirm_doses=False, can_view_diagnoses=True)
        elder = await caregiver.get(f"/api/v1/elders/{elder_id}")
        assert elder.status_code == 200
        assert elder.json()["diagnosed_conditions"] == ["Thông tin chẩn đoán"]
        assert elder.json()["current_medications_note"] is None
        assert (await caregiver.get(f"/api/v1/elders/{elder_id}/medication-schedules")).status_code == 403
        assert (await caregiver.get("/api/v1/dose-occurrences")).json() == []
        await assign(client, elder_id, caregiver_id, can_view_medications=True, can_confirm_doses=False, can_view_diagnoses=False)
        elder = await caregiver.get(f"/api/v1/elders/{elder_id}")
        assert elder.json()["diagnosed_conditions"] is None
        assert elder.json()["current_medications_note"] == "Thông tin thuốc cần bảo vệ"
        schedule_id = await schedule(client, elder_id)
        async with db_factory() as db:
            occurrence = DoseOccurrence(schedule_id=uuid.UUID(schedule_id), scheduled_for=datetime.now(UTC), status=DoseStatus.DUE)
            db.add(occurrence)
            await db.commit()
            occurrence_id = occurrence.id
        doses = await caregiver.get("/api/v1/dose-occurrences")
        assert len(doses.json()) == 1 and doses.json()[0]["can_respond"] is False
        response = await caregiver.post(f"/api/v1/dose-occurrences/{occurrence_id}/responses", json={"status": "ADMINISTERED"})
        assert response.status_code == 403
        assert (await caregiver.patch(f"/api/v1/elders/{elder_id}", json={"full_name": "Changed"})).status_code == 403
        assert (await caregiver.get(f"/api/v1/elders/{elder_id}/caregivers")).status_code == 403
        other = await client.post("/api/v1/elders", json={"full_name": "Chưa phân công"})
        assert (await caregiver.get(f"/api/v1/elders/{other.json()['id']}")).status_code == 404
        assert (await caregiver.get("/api/v1/elders", headers={"X-Care-Group-ID": str(uuid.uuid4())})).json()["error"]["code"] == "GROUP_ACCESS_DENIED"


async def test_foreign_household_ids_never_grant_member_invitation_or_elder_access(client):
    _, group_id, elder_id = await household(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        registered = await other.post("/api/v1/auth/register", json=account("other-owner@example.com"))
        other_group = await other.post("/api/v1/care-groups", json={"name": "Nhóm khác"})
        invitation = await other.post("/api/v1/care-groups/current/invitations", json={"email": "foreign-helper@example.com"})
        other_id = registered.json()["user"]["id"]
        assert (await client.delete(f"/api/v1/care-groups/current/members/{other_id}")).status_code == 404
        assert (await client.delete(f"/api/v1/care-groups/current/invitations/{invitation.json()['id']}")).status_code == 404
        assert (await client.put(f"/api/v1/elders/{elder_id}/caregivers/{other_id}", json={})).status_code == 404
        assert (await other.patch(f"/api/v1/elders/{elder_id}", json={"full_name": "Unauthorized"})).status_code == 404
        denied = await other.get("/api/v1/care-groups/current/members", headers={"X-Care-Group-ID": group_id})
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "GROUP_ACCESS_DENIED"
        assert (await client.get("/api/v1/elders", headers={"X-Care-Group-ID": other_group.json()["id"]})).status_code == 403


async def test_member_removal_and_unassignment_clear_schedule_access_immediately(client, db_factory):
    owner_id, _, elder_id = await household(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as caregiver:
        caregiver_id, _ = await helper(client, caregiver)
        await assign(client, elder_id, caregiver_id)
        schedule_id = await schedule(client, elder_id, caregiver_id)
        removed = await client.delete(f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}")
        assert removed.status_code == 204
        assert (await caregiver.get("/api/v1/elders")).json() == []
        assert (await caregiver.get(f"/api/v1/elders/{elder_id}")).status_code == 404
        async with db_factory() as db:
            assert (await db.get(MedicationSchedule, uuid.UUID(schedule_id))).assigned_caregiver_user_id is None
        await assign(client, elder_id, caregiver_id)
        assert (await client.patch(f"/api/v1/medication-schedules/{schedule_id}", json={"assigned_caregiver_user_id": caregiver_id})).status_code == 200
        assert (await client.delete(f"/api/v1/care-groups/current/members/{owner_id}")).status_code == 409
        assert (await client.delete(f"/api/v1/care-groups/current/members/{caregiver_id}")).status_code == 204
        me = await caregiver.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["groups"] == []
        denied = await caregiver.get("/api/v1/elders")
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "GROUP_ACCESS_DENIED"
        assert (await client.put(f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}", json={})).status_code == 404
        async with db_factory() as db:
            assert await db.scalar(select(CaregiverAssignment.id).where(CaregiverAssignment.caregiver_user_id == uuid.UUID(caregiver_id))) is None
            schedules = (await db.scalars(select(MedicationSchedule))).all()
            assert all(row.assigned_caregiver_user_id is None for row in schedules)


async def test_elder_archive_cancels_pending_doses_and_attempts_preserves_history(client, db_factory):
    owner_id, _, elder_id = await household(client)
    schedule_id = await schedule(client, elder_id)
    async with db_factory() as db:
        pending = DoseOccurrence(schedule_id=uuid.UUID(schedule_id), scheduled_for=datetime.now(UTC), status=DoseStatus.DUE, next_action_at=datetime.now(UTC))
        historical = DoseOccurrence(schedule_id=uuid.UUID(schedule_id), scheduled_for=datetime.now(UTC) - timedelta(days=1), status=DoseStatus.ADMINISTERED)
        unconfirmed = DoseOccurrence(schedule_id=uuid.UUID(schedule_id), scheduled_for=datetime.now(UTC) - timedelta(hours=2), status=DoseStatus.UNCONFIRMED)
        db.add_all([pending, historical, unconfirmed])
        await db.flush()
        attempt = NotificationAttempt(occurrence_id=pending.id, recipient_user_id=uuid.UUID(owner_id), kind=NotificationKind.REMINDER, ordinal=1, next_attempt_at=datetime.now(UTC))
        escalation = NotificationAttempt(occurrence_id=unconfirmed.id, recipient_user_id=uuid.UUID(owner_id), kind=NotificationKind.ESCALATION, ordinal=1, next_attempt_at=datetime.now(UTC))
        db.add_all([attempt, escalation])
        await db.commit()
        pending_id, history_id, attempt_id = pending.id, historical.id, attempt.id
        unconfirmed_id, escalation_id = unconfirmed.id, escalation.id
    archived = await client.patch(f"/api/v1/elders/{elder_id}", json={"is_active": False})
    assert archived.status_code == 200 and archived.json()["is_active"] is False
    async with db_factory() as db:
        pending = await db.get(DoseOccurrence, pending_id)
        assert pending.status == DoseStatus.CANCELLED and pending.next_action_at is None
        assert (await db.get(DoseOccurrence, history_id)).status == DoseStatus.ADMINISTERED
        assert (await db.get(DoseOccurrence, unconfirmed_id)).status == DoseStatus.UNCONFIRMED
        attempt = await db.get(NotificationAttempt, attempt_id)
        assert attempt.delivery_attempt_count == 3 and attempt.next_attempt_at is None
        escalation = await db.get(NotificationAttempt, escalation_id)
        assert escalation.delivery_attempt_count == 3 and escalation.next_attempt_at is None


async def test_future_birth_dates_are_rejected_for_creation_and_update(client):
    _, _, elder_id = await household(client)
    future = (date.today() + timedelta(days=1)).isoformat()
    assert (await client.post("/api/v1/elders", json={"full_name": "Future", "date_of_birth": future})).status_code == 422
    assert (await client.patch(f"/api/v1/elders/{elder_id}", json={"date_of_birth": future})).status_code == 422
    cleared = await client.patch(f"/api/v1/elders/{elder_id}", json={"date_of_birth": None, "diagnosed_conditions": [" A ", "A", ""]})
    assert cleared.status_code == 200
    assert cleared.json()["date_of_birth"] is None and cleared.json()["diagnosed_conditions"] == ["A"]


async def test_new_telegram_link_invalidates_older_unused_links(client, db_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_bot_username", "fictional_test_bot")
    await client.post("/api/v1/auth/register", json=account())
    first = await client.post("/api/v1/auth/telegram-link")
    second = await client.post("/api/v1/auth/telegram-link")
    assert first.status_code == second.status_code == 200
    assert first.json()["deep_link"] != second.json()["deep_link"]
    async with db_factory() as db:
        codes = (await db.scalars(select(TelegramLinkCode).order_by(TelegramLinkCode.created_at, TelegramLinkCode.id))).all()
        assert len(codes) == 2
        assert sum(row.used_at is None for row in codes) == 1
