import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select

from app.models import (
    AuditLog,
    DoseOccurrence,
    DoseResponse,
    DoseStatus,
    MedicationSchedule,
    NotificationAttempt,
)
from app.services.scheduler import ensure_occurrences, process_due_occurrences


async def login(client, email: str, password: str):
    result = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert result.status_code == 200, result.text
    return result.json()


async def test_invitation_assignment_schedule_and_idempotent_response(client, db_factory):
    owner_password = "owner-secure-password"
    owner = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Chu nha",
            "email": "owner@care.example.com",
            "password": owner_password,
        },
    )
    assert owner.status_code == 201, owner.text
    group = await client.post(
        "/api/v1/care-groups", json={"name": "Gia dinh test"}
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
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
    invalid_elder_update = await client.patch(
        f"/api/v1/elders/{elder_id}",
        headers=headers,
        json={"full_name": None},
    )
    assert invalid_elder_update.status_code == 422
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
    invalid_assignment = await client.put(
        f"/api/v1/elders/{elder_id}/caregivers/{caregiver_id}",
        headers=headers,
        json={
            "can_view_medications": False,
            "can_confirm_doses": True,
            "can_view_diagnoses": False,
        },
    )
    assert invalid_assignment.status_code == 422
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
    async with db_factory() as db:
        assignment_audit = await db.scalar(
            select(AuditLog).where(AuditLog.action == "CAREGIVER_ASSIGNED")
        )
        assert assignment_audit is not None
        assert assignment_audit.entity_id != "None"

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
    schedule_id = schedule.json()["id"]

    # Future occurrences are derived from the schedule. Editing the schedule
    # must remove them so the worker can recreate the correct times, while a
    # historic occurrence must stay attached to the old schedule version.
    async with db_factory() as db:
        db.add_all(
            [
                DoseOccurrence(
                    schedule_id=uuid.UUID(schedule_id),
                    scheduled_for=datetime.now(UTC) - timedelta(days=2),
                    status=DoseStatus.UNCONFIRMED,
                    next_action_at=None,
                ),
                DoseOccurrence(
                schedule_id=uuid.UUID(schedule_id),
                scheduled_for=datetime.now(UTC) + timedelta(days=2),
                status=DoseStatus.SCHEDULED,
                next_action_at=datetime.now(UTC) + timedelta(days=2),
                ),
            ]
        )
        await db.commit()
    changed = await client.patch(
        f"/api/v1/medication-schedules/{schedule_id}",
        headers=headers,
        json={"time_of_day": "08:05:00"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["id"] != schedule_id
    assert changed.json()["time_of_day"] == "08:05:00"
    invalid_schedule_update = await client.patch(
        f"/api/v1/medication-schedules/{schedule_id}",
        headers=headers,
        json={"time_of_day": None},
    )
    assert invalid_schedule_update.status_code == 422
    async with db_factory() as db:
        remaining = (await db.scalars(select(DoseOccurrence))).all()
        assert len(remaining) == 1
        assert remaining[0].schedule_id == uuid.UUID(schedule_id)
        old_schedule = await db.get(MedicationSchedule, uuid.UUID(schedule_id))
        new_schedule = await db.get(
            MedicationSchedule, uuid.UUID(changed.json()["id"])
        )
        assert old_schedule is not None and old_schedule.is_active is False
        assert new_schedule is not None and new_schedule.is_active is True
        assert new_schedule.time_of_day == time(8, 5)

    async with db_factory() as db:
        created = await ensure_occurrences(db, datetime(2026, 9, 20, 0, 30, tzinfo=UTC))
        assert created >= 1
        processed = await process_due_occurrences(
            db, datetime(2026, 9, 20, 1, 6, tzinfo=UTC)
        )
        assert processed == 1

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
    assert dose["can_respond"] is True
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
        attempt = await db.scalar(select(NotificationAttempt))
        assert attempt is not None
        assert attempt.delivery_attempt_count == 3
        assert attempt.next_attempt_at is None
