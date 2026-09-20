from datetime import UTC, datetime

from sqlalchemy import select

from app.models import DoseOccurrence, DoseStatus, NotificationAttempt, NotificationKind
from app.services.scheduler import ensure_occurrences, process_due_occurrences


async def test_worker_marks_overdue_unconfirmed_and_does_not_duplicate_escalation(
    client, db_factory
):
    owner = await client.post(
        "/api/v1/auth/register-owner",
        json={
            "full_name": "Owner",
            "email": "worker-owner@example.com",
            "password": "strong-worker-password",
            "care_group_name": "Worker family",
        },
    )
    group_id = owner.json()["default_group_id"]
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
