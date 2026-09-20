from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import DoseResponse
from app.services.scheduler import ensure_occurrences


async def login(client, email: str, password: str):
    result = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert result.status_code == 200, result.text
    return result.json()


async def test_invitation_assignment_schedule_and_idempotent_response(client, db_factory):
    owner_password = "owner-secure-password"
    owner = await client.post(
        "/api/v1/auth/register-owner",
        json={
            "full_name": "Chu nha",
            "email": "owner@care.example.com",
            "password": owner_password,
            "care_group_name": "Gia dinh test",
        },
    )
    assert owner.status_code == 201, owner.text
    group_id = owner.json()["default_group_id"]
    headers = {"X-Care-Group-ID": group_id}
    elder = await client.post(
        "/api/v1/elders",
        headers=headers,
        json={
            "full_name": "Cu Ong",
            "date_of_birth": "1940-01-01",
            "diagnosed_conditions": ["Tang huyet ap"],
            "current_medications_note": "Theo don bac si",
        },
    )
    elder_id = elder.json()["id"]
    invitation = await client.post(
        "/api/v1/care-groups/current/invitations",
        headers=headers,
        json={"email": "helper@care.example.com", "expires_in_hours": 24},
    )
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()["invitation_token"]

    await client.post("/api/v1/auth/logout")
    wrong_email = await client.post(
        "/api/v1/auth/register-caregiver",
        json={
            "full_name": "Sai email",
            "email": "wrong@care.example.com",
            "password": "caregiver-password",
            "invitation_token": token,
        },
    )
    assert wrong_email.status_code == 403
    caregiver = await client.post(
        "/api/v1/auth/register-caregiver",
        json={
            "full_name": "Co Lan",
            "email": "helper@care.example.com",
            "password": "caregiver-password",
            "invitation_token": token,
        },
    )
    assert caregiver.status_code == 201, caregiver.text
    caregiver_id = caregiver.json()["user"]["id"]

    assert (await client.get(f"/api/v1/elders/{elder_id}", headers=headers)).status_code == 404
    cannot_create = await client.post(
        "/api/v1/elders", headers=headers, json={"full_name": "Khong duoc tao"}
    )
    assert cannot_create.status_code == 403

    await client.post("/api/v1/auth/logout")
    await login(client, "owner@care.example.com", owner_password)
    assignment = await client.put(
        f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}",
        headers=headers,
        json={
            "can_view_medications": True,
            "can_confirm_doses": True,
            "can_view_diagnoses": False,
        },
    )
    assert assignment.status_code == 200, assignment.text
    schedule = await client.post(
        f"/api/v1/elders/{elder_id}/medication-schedules",
        headers=headers,
        json={
            "medication_name": "Thuoc A",
            "dose_amount": "1",
            "dose_unit": "vien",
            "start_date": "2026-09-20",
            "time_of_day": "08:00:00",
            "days_of_week": [6],
            "timezone": "Asia/Ho_Chi_Minh",
            "reminder_offsets_minutes": [0, 15, 30, 45],
            "escalation_after_minutes": 60,
            "assigned_caregiver_user_id": caregiver_id,
        },
    )
    assert schedule.status_code == 201, schedule.text

    async with db_factory() as db:
        created = await ensure_occurrences(db, datetime(2026, 9, 20, 0, 30, tzinfo=UTC))
        assert created >= 1

    await client.post("/api/v1/auth/logout")
    await login(client, "helper@care.example.com", "caregiver-password")
    visible_elder = await client.get(f"/api/v1/elders/{elder_id}", headers=headers)
    assert visible_elder.status_code == 200
    assert visible_elder.json()["diagnosed_conditions"] is None

    caregiver_schedule_create = await client.post(
        f"/api/v1/elders/{elder_id}/medication-schedules",
        headers=headers,
        json={
            "medication_name": "Khong duoc tao",
            "dose_amount": "1",
            "dose_unit": "vien",
            "start_date": "2026-09-20",
            "time_of_day": "09:00:00",
        },
    )
    assert caregiver_schedule_create.status_code == 403

    doses = await client.get(
        "/api/v1/dose-occurrences?date=2026-09-20", headers=headers
    )
    assert doses.status_code == 200, doses.text
    dose = doses.json()[0]
    payload = {"status": "ADMINISTERED", "note": "Da cho uong"}
    first_response = await client.post(
        f"/api/v1/dose-occurrences/{dose['id']}/responses", headers=headers, json=payload
    )
    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["status"] == "ADMINISTERED"
    retry = await client.post(
        f"/api/v1/dose-occurrences/{dose['id']}/responses", headers=headers, json=payload
    )
    assert retry.status_code == 200, retry.text

    async with db_factory() as db:
        count = await db.scalar(select(func.count()).select_from(DoseResponse))
        assert count == 1
