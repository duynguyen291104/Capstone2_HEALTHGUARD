from datetime import UTC, datetime

from sqlalchemy import select

from app.models import DoseOccurrence, DoseStatus, NotificationAttempt, NotificationKind
from app.services.scheduler import ensure_occurrences, process_due_occurrences


async def register_owner_group(client, *, full_name: str, email: str, group_name: str):
    owner = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": full_name,
            "email": email,
            "password": "strong-worker-password",
        },
    )
    assert owner.status_code == 201, owner.text
    group = await client.post("/api/v1/care-groups", json={"name": group_name})
    assert group.status_code == 201, group.text
    return group.json()["id"]


async def test_worker_marks_overdue_unconfirmed_and_does_not_duplicate_escalation(
    client, db_factory
):
    group_id = await register_owner_group(
        client,
        full_name="Owner",
        email="worker-owner@example.com",
        group_name="Worker family",
    )
    headers = {"X-Care-Group-ID": group_id}
    elder = await client.post(
        "/api/v1/elders", headers=headers, json={"full_name": "Cu Ba"}
    )
    schedule = await client.post(
        f"/api/v1/elders/{elder.json()['id']}/medication-schedules",
        headers=headers,
        json={
            "medication_name": "Thuoc B",
            "dose_amount": 1,
            "dose_unit": "vien",
            "start_date": "2026-09-20",
            "time_of_day": "08:00:00",
            "days_of_week": [6],
            "timezone": "Asia/Ho_Chi_Minh",
            "reminder_offsets_minutes": [0, 15, 30, 45],
            "escalation_after_minutes": 60,
        },
    )
    assert schedule.status_code == 201, schedule.text

    async with db_factory() as db:
        await ensure_occurrences(db, datetime(2026, 9, 20, 0, 30, tzinfo=UTC))
        processed = await process_due_occurrences(
            db, datetime(2026, 9, 20, 2, 1, tzinfo=UTC)
        )
        assert processed == 1
        occurrence = await db.scalar(select(DoseOccurrence))
        assert occurrence.status == DoseStatus.UNCONFIRMED
        attempts = (await db.scalars(select(NotificationAttempt))).all()
        assert len(attempts) == 1
        assert attempts[0].kind == NotificationKind.ESCALATION

        assert (
            await process_due_occurrences(db, datetime(2026, 9, 20, 2, 2, tzinfo=UTC))
        ) == 0
        attempts = (await db.scalars(select(NotificationAttempt))).all()
        assert len(attempts) == 1


async def test_worker_skips_missed_reminders_instead_of_replaying_a_burst(client, db_factory):
    group_id = await register_owner_group(
        client,
        full_name="Owner late worker",
        email="late-worker-owner@example.com",
        group_name="Late worker family",
    )
    headers = {"X-Care-Group-ID": group_id}
    elder = await client.post(
        "/api/v1/elders", headers=headers, json={"full_name": "Cu Ong"}
    )
    schedule = await client.post(
        f"/api/v1/elders/{elder.json()['id']}/medication-schedules",
        headers=headers,
        json={
            "medication_name": "Thuoc C",
            "dose_amount": 1,
            "dose_unit": "vien",
            "start_date": "2026-09-20",
            "time_of_day": "08:00:00",
            "days_of_week": [6],
            "timezone": "Asia/Ho_Chi_Minh",
            "reminder_offsets_minutes": [0, 15, 30, 45],
            "escalation_after_minutes": 60,
        },
    )
    assert schedule.status_code == 201, schedule.text

    async with db_factory() as db:
        await ensure_occurrences(db, datetime(2026, 9, 20, 0, 30, tzinfo=UTC))
        processed = await process_due_occurrences(
            db, datetime(2026, 9, 20, 1, 46, tzinfo=UTC)
        )
        assert processed == 1
        occurrence = await db.scalar(select(DoseOccurrence))
        assert occurrence.status == DoseStatus.DUE
        assert occurrence.reminder_count == 4
        assert occurrence.next_action_at == datetime(2026, 9, 20, 2, 0)
        attempts = (await db.scalars(select(NotificationAttempt))).all()
        assert len(attempts) == 1
        assert attempts[0].ordinal == 4
        assert (
            await process_due_occurrences(db, datetime(2026, 9, 20, 1, 47, tzinfo=UTC))
        ) == 0
        attempts = (await db.scalars(select(NotificationAttempt))).all()
        assert len(attempts) == 1


async def test_stopping_schedule_cancels_due_work_and_pending_notifications(client, db_factory):
    group_id = await register_owner_group(
        client,
        full_name="Owner stop schedule",
        email="stop-schedule-owner@example.com",
        group_name="Stop schedule family",
    )
    headers = {"X-Care-Group-ID": group_id}
    elder = await client.post(
        "/api/v1/elders", headers=headers, json={"full_name": "Cu Ba"}
    )
    schedule = await client.post(
        f"/api/v1/elders/{elder.json()['id']}/medication-schedules",
        headers=headers,
        json={
            "medication_name": "Thuoc D",
            "dose_amount": 1,
            "dose_unit": "vien",
            "start_date": "2026-09-20",
            "time_of_day": "08:00:00",
            "days_of_week": [6],
            "timezone": "Asia/Ho_Chi_Minh",
            "reminder_offsets_minutes": [0, 15, 30, 45],
            "escalation_after_minutes": 60,
        },
    )
    assert schedule.status_code == 201, schedule.text

    async with db_factory() as db:
        await ensure_occurrences(db, datetime(2026, 9, 20, 0, 30, tzinfo=UTC))
        assert await process_due_occurrences(
            db, datetime(2026, 9, 20, 1, 1, tzinfo=UTC)
        ) == 1

    stopped = await client.delete(
        f"/api/v1/medication-schedules/{schedule.json()['id']}", headers=headers
    )
    assert stopped.status_code == 204, stopped.text

    async with db_factory() as db:
        occurrence = await db.scalar(select(DoseOccurrence))
        assert occurrence.status == DoseStatus.CANCELLED
        assert occurrence.next_action_at is None
        attempt = await db.scalar(select(NotificationAttempt))
        assert attempt.delivery_attempt_count == 3
        assert attempt.next_attempt_at is None
        assert await process_due_occurrences(
            db, datetime(2026, 9, 20, 1, 16, tzinfo=UTC)
        ) == 0
