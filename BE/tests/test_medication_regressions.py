"""Medication safety regressions, isolated SQLite and fake Telegram only."""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog, CareGroupMember, CaregiverAssignment, DoseOccurrence, DoseResponse,
    DoseStatus, GroupRole, MedicationSchedule, NotificationAttempt, NotificationKind, User,
)
from app.routers import medication_schedules
from app.schemas import ScheduleCreate
from app.services import scheduler
from app.services.telegram import TelegramResult


async def setup_schedule(client, *, clock="08:00:30", days=None):
    owner = await client.post("/api/v1/auth/register", json={
        "full_name": "Fictional owner", "email": "medication-review@example.com",
        "password": "isolated-medication-password",
    })
    assert owner.status_code == 201, owner.text
    group = await client.post("/api/v1/care-groups", json={"name": "Regression only"})
    headers = {"X-Care-Group-ID": group.json()["id"]}
    elder = await client.post("/api/v1/elders", headers=headers, json={"full_name": "Fictional elder"})
    schedule = await client.post(f"/api/v1/elders/{elder.json()['id']}/medication-schedules", headers=headers, json={
        "medication_name": "Demo 10mg", "dose_amount": "10", "dose_unit": "vien",
        "start_date": "2026-10-01", "time_of_day": clock,
        "days_of_week": days if days is not None else list(range(7)),
    })
    assert schedule.status_code == 201, schedule.text
    return headers, schedule.json(), owner.json()["user"]["id"]


def set_edit_clock(monkeypatch, instant):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(medication_schedules, "datetime", FixedClock)


@pytest.mark.parametrize("clock", ["08:00:00+07:00", "08:00:00Z"])
def test_schedule_clock_cannot_silently_discard_timezone(clock):
    with pytest.raises(ValueError, match="local clock"):
        ScheduleCreate(medication_name="Demo", dose_amount=1, dose_unit="vien", start_date=date(2026, 10, 1), time_of_day=clock)


async def test_worker_restart_records_missed_dose_and_exact_second_timezone(client, db_factory):
    _, schedule, _ = await setup_schedule(client, days=[0])
    now = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)  # Monday 10:00 Vietnam
    async with db_factory() as db:
        assert await scheduler.ensure_occurrences(db, now) >= 1
        assert await scheduler.ensure_occurrences(db, now) == 0
        occurrence = await db.scalar(select(DoseOccurrence).where(DoseOccurrence.schedule_id == uuid.UUID(schedule["id"])))
        assert occurrence.scheduled_for == datetime(2026, 10, 5, 1, 0, 30)
        assert await scheduler.process_due_occurrences(db, now) == 1
        assert occurrence.status == DoseStatus.UNCONFIRMED
        assert occurrence.next_action_at is None
        assert await db.scalar(select(func.count()).select_from(NotificationAttempt)) == 1


async def test_edit_keeps_due_history_without_second_same_day_dose(client, db_factory, monkeypatch):
    headers, schedule, _ = await setup_schedule(client)
    now = datetime(2026, 10, 5, 1, 20, tzinfo=UTC)  # Already after 08:00:30
    async with db_factory() as db:
        await scheduler.ensure_occurrences(db, datetime(2026, 10, 5, 0, 50, tzinfo=UTC))
        await scheduler.process_due_occurrences(db, now)
        due_id = await db.scalar(select(DoseOccurrence.id).where(DoseOccurrence.schedule_id == uuid.UUID(schedule["id"]), DoseOccurrence.status == DoseStatus.DUE))
    set_edit_clock(monkeypatch, now)
    changed = await client.patch(f"/api/v1/medication-schedules/{schedule['id']}", headers=headers, json={"time_of_day": "09:00:30", "dose_amount": "2"})
    assert changed.status_code == 200, changed.text
    new_id = changed.json()["id"]
    async with db_factory() as db:
        await scheduler.ensure_occurrences(db, now)
        replacement = (await db.scalars(select(DoseOccurrence).where(DoseOccurrence.schedule_id == uuid.UUID(new_id)))).all()
        assert replacement and all(item.scheduled_for.date() >= date(2026, 10, 6) for item in replacement)
        old_due = await db.get(DoseOccurrence, due_id)
        assert old_due.status == DoseStatus.DUE
        await scheduler.process_due_occurrences(db, datetime(2026, 10, 5, 2, 1, tzinfo=UTC))
        assert old_due.status == DoseStatus.UNCONFIRMED
        previous = await db.get(MedicationSchedule, uuid.UUID(schedule["id"]))
        assert previous.is_active is False
        audit = await db.scalar(select(AuditLog).where(AuditLog.entity_id == new_id, AuditLog.action == "MEDICATION_SCHEDULE_UPDATED"))
        assert datetime.fromisoformat(audit.details["effective_from"]) == datetime(2026, 10, 5, 17, 0, tzinfo=UTC)
    # Re-editing that deferred version must not reintroduce today's dose.
    changed_again = await client.patch(f"/api/v1/medication-schedules/{new_id}", headers=headers, json={"time_of_day": "10:00:00"})
    assert changed_again.status_code == 200, changed_again.text
    async with db_factory() as db:
        await scheduler.ensure_occurrences(db, now)
        same_day = await db.scalar(select(DoseOccurrence.id).where(DoseOccurrence.schedule_id == uuid.UUID(changed_again.json()["id"]), DoseOccurrence.scheduled_for < datetime(2026, 10, 5, 17)))
        assert same_day is None
    stopped = await client.delete(f"/api/v1/medication-schedules/{changed_again.json()['id']}", headers=headers)
    assert stopped.status_code == 204
    async with db_factory() as db:
        old_due = await db.get(DoseOccurrence, due_id)
        assert old_due.status == DoseStatus.CANCELLED
        assert old_due.next_action_at is None
        assert await scheduler.dispatch_notifications(db, now + timedelta(hours=2)) == 0


async def test_dose_response_rejects_future_and_earlier_timestamp_and_changed_retry(client, db_factory):
    headers, schedule, _ = await setup_schedule(client)
    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=1)
    async with db_factory() as db:
        occurrence = DoseOccurrence(schedule_id=uuid.UUID(schedule["id"]), scheduled_for=past, status=DoseStatus.DUE, next_action_at=past)
        corrupt_future_due = DoseOccurrence(schedule_id=uuid.UUID(schedule["id"]), scheduled_for=future, status=DoseStatus.DUE, next_action_at=future)
        db.add_all([occurrence, corrupt_future_due])
        await db.commit()
        occurrence_id, future_id = occurrence.id, corrupt_future_due.id
    url = f"/api/v1/dose-occurrences/{occurrence_id}/responses"
    for instant in [past - timedelta(seconds=1), future]:
        rejected = await client.post(url, headers=headers, json={"status": "ADMINISTERED", "administered_at": instant.isoformat()})
        assert rejected.status_code == 422, rejected.text
    not_due = await client.post(f"/api/v1/dose-occurrences/{future_id}/responses", headers=headers, json={"status": "ADMINISTERED"})
    assert not_due.status_code == 409
    invalid = await client.post(url, headers=headers, json={"status": "CANNOT_ADMINISTER", "reason_code": "  "})
    assert invalid.status_code == 422
    invalid = await client.post(url, headers=headers, json={"status": "CANNOT_ADMINISTER", "reason_code": "refused", "administered_at": past.isoformat()})
    assert invalid.status_code == 422
    payload = {"status": "ADMINISTERED", "administered_at": (past + timedelta(minutes=1)).isoformat(), "note": "Reviewed"}
    saved = await client.post(url, headers=headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["can_respond"] is False
    retry = await client.post(url, headers=headers, json=payload)
    assert retry.status_code == 200
    changed = await client.post(url, headers=headers, json={**payload, "administered_at": (past + timedelta(minutes=2)).isoformat()})
    assert changed.status_code == 409
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(DoseResponse)) == 1


async def test_outbox_drops_old_reminder_retries_then_sends_only_current(client, db_factory, monkeypatch):
    _, schedule, owner_id = await setup_schedule(client, days=[0])
    sent = []
    async def fake_send(**kwargs):
        sent.append(kwargs)
        return TelegramResult(True, message_id="fake-delivery")
    monkeypatch.setattr(scheduler.telegram_client, "send_message", fake_send)
    now = datetime(2026, 10, 5, 1, 16, tzinfo=UTC)
    async with db_factory() as db:
        owner = await db.get(User, uuid.UUID(owner_id))
        owner.telegram_chat_id = "123456"
        await db.commit()
        await scheduler.ensure_occurrences(db, datetime(2026, 10, 5, 0, 50, tzinfo=UTC))
        await scheduler.process_due_occurrences(db, datetime(2026, 10, 5, 1, 1, tzinfo=UTC))
        await scheduler.process_due_occurrences(db, now)
        assert await scheduler.dispatch_notifications(db, now) == 2
        assert len(sent) == 1
        assert "10 vien" in sent[0]["text"]
        attempts = (await db.scalars(select(NotificationAttempt).order_by(NotificationAttempt.ordinal))).all()
        assert attempts[0].delivery_attempt_count == 3 and not attempts[0].delivered
        assert attempts[1].delivered
        assert await scheduler.dispatch_notifications(db, now + timedelta(minutes=1)) == 0


async def test_outbox_checks_removed_recipient_and_falls_back_to_owner(client, db_factory, monkeypatch):
    headers, schedule, owner_id = await setup_schedule(client, days=[0])
    sent = []
    async def fake_send(**kwargs):
        sent.append(kwargs)
        return TelegramResult(True, message_id="fake-delivery")
    monkeypatch.setattr(scheduler.telegram_client, "send_message", fake_send)
    async with db_factory() as db:
        caregiver = User(email="removed-caregiver@example.com", full_name="Removed helper", password_hash="unused-test-only", telegram_chat_id="456789")
        db.add(caregiver)
        await db.flush()
        db.add(CareGroupMember(group_id=uuid.UUID(headers["X-Care-Group-ID"]), user_id=caregiver.id, role=GroupRole.CAREGIVER))
        db.add(CaregiverAssignment(elder_id=uuid.UUID(schedule["elder_id"]), caregiver_user_id=caregiver.id, assigned_by_user_id=uuid.UUID(owner_id)))
        await db.commit()
        caregiver_id = caregiver.id
        await scheduler.ensure_occurrences(db, datetime(2026, 10, 5, 0, 50, tzinfo=UTC))
        await scheduler.process_due_occurrences(db, datetime(2026, 10, 5, 1, 1, tzinfo=UTC))
    removed = await client.delete(f"/api/v1/care-groups/current/members/{caregiver_id}", headers=headers)
    assert removed.status_code == 204
    async with db_factory() as db:
        await scheduler.dispatch_notifications(db, datetime(2026, 10, 5, 1, 2, tzinfo=UTC))
        assert not sent
        await scheduler.process_due_occurrences(db, datetime(2026, 10, 5, 1, 16, tzinfo=UTC))
        await scheduler.dispatch_notifications(db, datetime(2026, 10, 5, 1, 16, tzinfo=UTC))
        assert len(sent) == 1
        assert sent[0]["chat_id"] is None  # Owner is fallback; production records unlinked delivery failure.


async def test_outbox_failure_retries_at_most_three_times(client, db_factory, monkeypatch):
    _, _, _ = await setup_schedule(client)
    calls = []
    async def fake_send(**kwargs):
        calls.append(kwargs)
        return TelegramResult(False, error="fake transient outage")
    monkeypatch.setattr(scheduler.telegram_client, "send_message", fake_send)
    now = datetime(2026, 10, 5, 1, 1, tzinfo=UTC)
    async with db_factory() as db:
        await scheduler.ensure_occurrences(db, now)
        await scheduler.process_due_occurrences(db, now)
        assert await scheduler.dispatch_notifications(db, now) == 1
        assert await scheduler.dispatch_notifications(db, now + timedelta(minutes=4)) == 0
        assert await scheduler.dispatch_notifications(db, now + timedelta(minutes=5)) == 1
        assert await scheduler.dispatch_notifications(db, now + timedelta(minutes=10)) == 1
        assert await scheduler.dispatch_notifications(db, now + timedelta(minutes=15)) == 0
        assert len(calls) == 3
        attempt = await db.scalar(select(NotificationAttempt))
        assert attempt.delivery_attempt_count == 3 and attempt.next_attempt_at is None


def test_integer_dose_text_does_not_drop_a_significant_zero():
    from types import SimpleNamespace
    medicine = SimpleNamespace(name="Demo", dose_amount=Decimal("10"), dose_unit="ml", elder=SimpleNamespace(full_name="Fictional"))
    attempt = SimpleNamespace(kind=NotificationKind.REMINDER, occurrence=SimpleNamespace(schedule=SimpleNamespace(medication=medicine, timezone="Asia/Ho_Chi_Minh"), scheduled_for=datetime(2026, 10, 5, 1, 0, 30, tzinfo=UTC)))
    text, _ = scheduler.notification_text(attempt)
    assert "10 ml" in text
    assert "08:00 05/10/2026" in text
