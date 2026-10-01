from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import (
    CareGroupMember,
    CaregiverAssignment,
    DoseOccurrence,
    DoseStatus,
    ElderProfile,
    GroupRole,
    Medication,
    MedicationSchedule,
    NotificationAttempt,
    NotificationKind,
    User,
)
from app.services.telegram import telegram_client


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def ensure_occurrences(db: AsyncSession, now: datetime | None = None) -> int:
    now = as_utc(now or datetime.now(UTC))
    horizon = now + timedelta(hours=get_settings().occurrence_horizon_hours)
    schedules = (
        await db.scalars(
            select(MedicationSchedule)
            .options(
                selectinload(MedicationSchedule.medication).selectinload(Medication.elder)
            )
            .join(Medication, Medication.id == MedicationSchedule.medication_id)
            .join(ElderProfile, ElderProfile.id == Medication.elder_id)
            .where(
                MedicationSchedule.is_active.is_(True),
                Medication.is_active.is_(True),
                ElderProfile.is_active.is_(True),
            )
        )
    ).all()
    values: list[dict] = []
    for schedule in schedules:
        timezone = ZoneInfo(schedule.timezone)
        first_day = (now - timedelta(hours=24)).astimezone(timezone).date()
        last_day = horizon.astimezone(timezone).date()
        cursor = first_day
        while cursor <= last_day:
            medication = schedule.medication
            if (
                cursor.weekday() in schedule.days_of_week
                and cursor >= medication.start_date
                and (medication.end_date is None or cursor <= medication.end_date)
            ):
                local_time = datetime.combine(cursor, schedule.time_of_day, timezone)
                scheduled_for = local_time.astimezone(UTC)
                escalation_at = scheduled_for + timedelta(
                    minutes=schedule.escalation_after_minutes
                )
                if scheduled_for <= horizon and escalation_at >= now:
                    values.append(
                        {
                            "id": uuid.uuid4(),
                            "schedule_id": schedule.id,
                            "scheduled_for": scheduled_for,
                            "status": DoseStatus.SCHEDULED,
                            "reminder_count": 0,
                            "next_action_at": scheduled_for,
                        }
                    )
            cursor += timedelta(days=1)
    if not values:
        return 0
    dialect = db.bind.dialect.name if db.bind else "postgresql"
    insert_fn = sqlite_insert if dialect == "sqlite" else pg_insert
    statement = insert_fn(DoseOccurrence).values(values).on_conflict_do_nothing(
        index_elements=["schedule_id", "scheduled_for"]
    )
    result = await db.execute(statement)
    await db.commit()
    return max(result.rowcount or 0, 0)


async def reminder_recipients(
    db: AsyncSession, schedule: MedicationSchedule
) -> list[uuid.UUID]:
    elder = schedule.medication.elder
    if schedule.assigned_caregiver_user_id:
        assigned = await db.scalar(
            select(CaregiverAssignment.caregiver_user_id)
            .join(
                CareGroupMember,
                CareGroupMember.user_id == CaregiverAssignment.caregiver_user_id,
            )
            .join(User, User.id == CaregiverAssignment.caregiver_user_id)
            .where(
                CaregiverAssignment.elder_id == elder.id,
                CaregiverAssignment.caregiver_user_id == schedule.assigned_caregiver_user_id,
                CaregiverAssignment.can_view_medications.is_(True),
                CaregiverAssignment.can_confirm_doses.is_(True),
                CareGroupMember.group_id == elder.group_id,
                CareGroupMember.role == GroupRole.CAREGIVER,
                User.is_active.is_(True),
                User.telegram_chat_id.is_not(None),
            )
        )
        if assigned:
            return [assigned]
        return await owner_recipients(db, elder.group_id)
    recipients = (
        await db.scalars(
            select(CaregiverAssignment.caregiver_user_id)
            .join(
                CareGroupMember,
                CareGroupMember.user_id == CaregiverAssignment.caregiver_user_id,
            )
            .join(User, User.id == CaregiverAssignment.caregiver_user_id)
            .where(
                CaregiverAssignment.elder_id == elder.id,
                CaregiverAssignment.can_view_medications.is_(True),
                CaregiverAssignment.can_confirm_doses.is_(True),
                CareGroupMember.group_id == elder.group_id,
                CareGroupMember.role == GroupRole.CAREGIVER,
                User.is_active.is_(True),
                User.telegram_chat_id.is_not(None),
            )
        )
    ).all()
    if recipients:
        return list(dict.fromkeys(recipients))
    return list(
        (
            await db.scalars(
                select(CareGroupMember.user_id)
                .join(User, User.id == CareGroupMember.user_id)
                .where(
                    CareGroupMember.group_id == elder.group_id,
                    CareGroupMember.role == GroupRole.OWNER,
                    User.is_active.is_(True),
                )
            )
        ).all()
    )


async def owner_recipients(db: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await db.scalars(
                select(CareGroupMember.user_id)
                .join(User, User.id == CareGroupMember.user_id)
                .where(
                    CareGroupMember.group_id == group_id,
                    CareGroupMember.role == GroupRole.OWNER,
                    User.is_active.is_(True),
                )
            )
        ).all()
    )


async def enqueue_attempts(
    db: AsyncSession,
    occurrence_id: uuid.UUID,
    recipients: list[uuid.UUID],
    kind: NotificationKind,
    ordinal: int,
    now: datetime,
) -> None:
    if not recipients:
        return
    values = [
        {
            "id": uuid.uuid4(),
            "occurrence_id": occurrence_id,
            "recipient_user_id": recipient_id,
            "kind": kind,
            "ordinal": ordinal,
            "delivered": False,
            "delivery_attempt_count": 0,
            "next_attempt_at": now,
        }
        for recipient_id in recipients
    ]
    dialect = db.bind.dialect.name if db.bind else "postgresql"
    insert_fn = sqlite_insert if dialect == "sqlite" else pg_insert
    await db.execute(
        insert_fn(NotificationAttempt)
        .values(values)
        .on_conflict_do_nothing(
            index_elements=["occurrence_id", "recipient_user_id", "kind", "ordinal"]
        )
    )


async def process_due_occurrences(
    db: AsyncSession, now: datetime | None = None, batch_size: int = 100
) -> int:
    now = as_utc(now or datetime.now(UTC))
    occurrences = (
        await db.scalars(
            select(DoseOccurrence)
            .options(
                selectinload(DoseOccurrence.schedule)
                .selectinload(MedicationSchedule.medication)
                .selectinload(Medication.elder)
            )
            .join(MedicationSchedule, MedicationSchedule.id == DoseOccurrence.schedule_id)
            .join(Medication, Medication.id == MedicationSchedule.medication_id)
            .join(ElderProfile, ElderProfile.id == Medication.elder_id)
            .where(
                DoseOccurrence.status.in_([DoseStatus.SCHEDULED, DoseStatus.DUE]),
                DoseOccurrence.next_action_at.is_not(None),
                DoseOccurrence.next_action_at <= now,
                MedicationSchedule.is_active.is_(True),
                Medication.is_active.is_(True),
                ElderProfile.is_active.is_(True),
            )
            .order_by(DoseOccurrence.next_action_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for occurrence in occurrences:
        schedule = occurrence.schedule
        scheduled_for = as_utc(occurrence.scheduled_for)
        escalation_at = scheduled_for + timedelta(minutes=schedule.escalation_after_minutes)
        if now >= escalation_at:
            occurrence.status = DoseStatus.UNCONFIRMED
            occurrence.next_action_at = None
            occurrence.escalated_at = now
            recipients = await owner_recipients(db, schedule.medication.elder.group_id)
            await enqueue_attempts(
                db,
                occurrence.id,
                recipients,
                NotificationKind.ESCALATION,
                1,
                now,
            )
            continue

        offsets = schedule.reminder_offsets_minutes
        applicable_ordinal = max(
            index + 1
            for index, offset in enumerate(offsets)
            if scheduled_for + timedelta(minutes=offset) <= now
        )
        ordinal = max(occurrence.reminder_count + 1, applicable_ordinal)
        ordinal = min(ordinal, len(offsets))
        recipients = await reminder_recipients(db, schedule)
        await enqueue_attempts(
            db,
            occurrence.id,
            recipients,
            NotificationKind.REMINDER,
            ordinal,
            now,
        )
        occurrence.status = DoseStatus.DUE
        occurrence.reminder_count = ordinal
        future_offsets = [
            offset
            for offset in offsets[ordinal:]
            if scheduled_for + timedelta(minutes=offset) > now
        ]
        occurrence.next_action_at = (
            scheduled_for + timedelta(minutes=future_offsets[0])
            if future_offsets
            else escalation_at
        )
    await db.commit()
    return len(occurrences)


def notification_text(attempt: NotificationAttempt) -> tuple[str, str]:
    occurrence = attempt.occurrence
    schedule = occurrence.schedule
    medication = schedule.medication
    elder = medication.elder
    local_time = as_utc(occurrence.scheduled_for).astimezone(ZoneInfo(schedule.timezone))
    amount = format(medication.dose_amount, "f").rstrip("0").rstrip(".")
    dose = f"{amount} {medication.dose_unit}"
    if attempt.kind == NotificationKind.REMINDER:
        text = (
            "HealthGuard nhắc lịch thuốc\n"
            f"Người được chăm sóc: {elder.full_name}\n"
            f"Thuốc: {medication.name} – {dose}\n"
            f"Giờ dự kiến: {local_time:%H:%M %d/%m/%Y}\n"
            "Hãy mở ứng dụng để xác nhận sau khi đã thực hiện."
        )
        return text, "Mở và xác nhận"
    text = (
        "HealthGuard cần người nhà kiểm tra\n"
        f"Chưa có xác nhận cho lịch thuốc của {elder.full_name}.\n"
        f"Thuốc: {medication.name} – {dose}\n"
        f"Giờ dự kiến: {local_time:%H:%M %d/%m/%Y}\n"
        "Trạng thái này chỉ có nghĩa là chưa được xác nhận."
    )
    return text, "Mở để kiểm tra"


async def dispatch_notifications(
    db: AsyncSession, now: datetime | None = None, batch_size: int = 100
) -> int:
    now = as_utc(now or datetime.now(UTC))
    attempts = (
        await db.scalars(
            select(NotificationAttempt)
            .options(
                selectinload(NotificationAttempt.recipient),
                selectinload(NotificationAttempt.occurrence)
                .selectinload(DoseOccurrence.schedule)
                .selectinload(MedicationSchedule.medication)
                .selectinload(Medication.elder),
            )
            .where(
                NotificationAttempt.delivered.is_(False),
                NotificationAttempt.delivery_attempt_count < 3,
                or_(
                    NotificationAttempt.next_attempt_at.is_(None),
                    NotificationAttempt.next_attempt_at <= now,
                ),
            )
            .order_by(NotificationAttempt.sent_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    settings = get_settings()
    for attempt in attempts:
        occurrence = attempt.occurrence
        expected_status = (
            DoseStatus.DUE
            if attempt.kind == NotificationKind.REMINDER
            else DoseStatus.UNCONFIRMED
        )
        if occurrence.status != expected_status:
            attempt.delivery_attempt_count = 3
            attempt.next_attempt_at = None
            attempt.error = "Notification no longer applies to this dose"
            continue

        schedule = occurrence.schedule
        if (
            not schedule.is_active
            or not schedule.medication.is_active
            or not schedule.medication.elder.is_active
        ):
            attempt.delivery_attempt_count = 3
            attempt.next_attempt_at = None
            attempt.error = "Medication schedule is inactive"
            continue
        eligible_recipients = (
            await reminder_recipients(db, schedule)
            if attempt.kind == NotificationKind.REMINDER
            else await owner_recipients(db, schedule.medication.elder.group_id)
        )
        if attempt.recipient_user_id not in eligible_recipients:
            attempt.delivery_attempt_count = 3
            attempt.next_attempt_at = None
            attempt.error = "Recipient no longer has access"
            continue

        text, label = notification_text(attempt)
        local_date = as_utc(occurrence.scheduled_for).astimezone(
            ZoneInfo(schedule.timezone)
        ).date().isoformat()
        url = (
            f"{settings.frontend_base_url.rstrip('/')}/hom-nay"
            f"?date={local_date}&occurrence={attempt.occurrence_id}"
        )
        result = await telegram_client.send_message(
            chat_id=attempt.recipient.telegram_chat_id,
            text=text,
            action_url=url,
            action_label=label,
        )
        attempt.delivery_attempt_count += 1
        attempt.delivered = result.delivered
        attempt.provider_message_id = result.message_id
        attempt.error = result.error
        if result.delivered:
            attempt.delivered_at = now
            attempt.next_attempt_at = None
        elif attempt.delivery_attempt_count < 3:
            attempt.next_attempt_at = now + timedelta(minutes=5)
        else:
            attempt.next_attempt_at = None
    await db.commit()
    return len(attempts)
