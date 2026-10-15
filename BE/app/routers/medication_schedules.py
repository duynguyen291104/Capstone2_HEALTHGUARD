import hashlib
import json
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from app.audit import add_audit
from app.dependencies import CurrentUser, DbSession, ElderAccessDep, GroupCtx
from app.errors import AppError, not_found
from app.models import (
    AuditLog,
    CareGroupMember,
    CaregiverAssignment,
    DoseOccurrence,
    DoseStatus,
    ElderProfile,
    GroupRole,
    Medication,
    MedicationSchedule,
    NotificationAttempt,
    User,
)
from app.schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate


router = APIRouter(tags=["medication-schedules"])


def schedule_out(schedule: MedicationSchedule) -> ScheduleOut:
    medication = schedule.medication
    return ScheduleOut(
        id=schedule.id,
        elder_id=medication.elder_id,
        medication_id=medication.id,
        medication_name=medication.name,
        dose_amount=medication.dose_amount,
        dose_unit=medication.dose_unit,
        instructions=medication.instructions,
        start_date=medication.start_date,
        end_date=medication.end_date,
        time_of_day=schedule.time_of_day,
        days_of_week=schedule.days_of_week,
        timezone=schedule.timezone,
        reminder_offsets_minutes=schedule.reminder_offsets_minutes,
        escalation_after_minutes=schedule.escalation_after_minutes,
        assigned_caregiver_user_id=schedule.assigned_caregiver_user_id,
        is_active=schedule.is_active and medication.is_active,
    )


async def validate_assigned_caregiver(
    db: DbSession,
    *,
    elder_id: uuid.UUID,
    group_id: uuid.UUID,
    caregiver_user_id: uuid.UUID | None,
) -> None:
    if caregiver_user_id is None:
        return
    # Assignment/removal uses this same member row lock. An owner must not
    # assign a schedule to a membership that is disappearing concurrently.
    membership = await db.scalar(
        select(CareGroupMember.id).where(
            CareGroupMember.group_id == group_id,
            CareGroupMember.user_id == caregiver_user_id,
            CareGroupMember.role == GroupRole.CAREGIVER,
        ).with_for_update()
    )
    if not membership:
        raise AppError(400, "INVALID_ASSIGNED_CAREGIVER", "Người chăm sóc không thuộc nhóm")
    valid = await db.scalar(
        select(CaregiverAssignment.id)
        .join(
            CareGroupMember,
            CareGroupMember.user_id == CaregiverAssignment.caregiver_user_id,
        )
        .where(
            CaregiverAssignment.elder_id == elder_id,
            CaregiverAssignment.caregiver_user_id == caregiver_user_id,
            CaregiverAssignment.can_view_medications.is_(True),
            CaregiverAssignment.can_confirm_doses.is_(True),
            CareGroupMember.group_id == group_id,
            CareGroupMember.role == GroupRole.CAREGIVER,
        )
    )
    if not valid:
        raise AppError(
            400,
            "INVALID_ASSIGNED_CAREGIVER",
            "Người chăm sóc phải được phân công và có quyền xác nhận thuốc",
        )


@router.get(
    "/elders/{elder_id}/medication-schedules", response_model=list[ScheduleOut]
)
async def list_schedules(access: ElderAccessDep, db: DbSession) -> list[ScheduleOut]:
    if access.assignment and not access.assignment.can_view_medications:
        raise AppError(403, "MEDICATION_ACCESS_DENIED", "Bạn không có quyền xem lịch thuốc")
    schedules = (
        await db.scalars(
            select(MedicationSchedule)
            .options(selectinload(MedicationSchedule.medication))
            .join(Medication, Medication.id == MedicationSchedule.medication_id)
            .where(Medication.elder_id == access.elder.id)
            .order_by(MedicationSchedule.time_of_day)
        )
    ).all()
    return [schedule_out(item) for item in schedules]


@router.post(
    "/elders/{elder_id}/medication-schedules",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    payload: ScheduleCreate,
    request: Request,
    access: ElderAccessDep,
    user: CurrentUser,
    db: DbSession,
) -> ScheduleOut:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được tạo lịch thuốc")
    if not access.elder.is_active:
        raise AppError(409, "ELDER_INACTIVE", "Hồ sơ đã ngừng theo dõi")
    # A batch import can lose a response after a successful commit. Reusing
    # its per-slot key must acknowledge that schedule, not create a second one.
    idempotency_key = request.headers.get("Idempotency-Key")
    fingerprint = None
    if idempotency_key:
        try:
            idempotency_key = str(uuid.UUID(idempotency_key))
        except ValueError:
            raise AppError(400, "INVALID_IDEMPOTENCY_KEY", "Mã lần lưu không hợp lệ") from None
        fingerprint = hashlib.sha256(json.dumps(
            {"elder_id": str(access.elder.id), "schedule": payload.model_dump(mode="json")},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        # PostgreSQL serializes concurrent retries by this authenticated user.
        # The audit and schedule are committed in the same transaction below.
        await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
        previous = await db.scalar(select(AuditLog).where(
            AuditLog.actor_user_id == user.id,
            AuditLog.group_id == access.context.group_id,
            AuditLog.action == "MEDICATION_SCHEDULE_CREATED",
            AuditLog.details["idempotency_key"].as_string() == idempotency_key,
        ))
        if previous:
            if previous.details.get("request_fingerprint") != fingerprint:
                raise AppError(409, "IMPORT_ALREADY_SAVED", "Lần lưu trước có nội dung khác. Tải lại danh sách lịch để kiểm tra trước khi tạo mới.")
            schedule = await get_schedule_for_group(uuid.UUID(previous.entity_id), access.context.group_id, db)
            return schedule_out(schedule)
    await validate_assigned_caregiver(
        db,
        elder_id=access.elder.id,
        group_id=access.context.group_id,
        caregiver_user_id=payload.assigned_caregiver_user_id,
    )
    medication = Medication(
        elder_id=access.elder.id,
        name=payload.medication_name.strip(),
        dose_amount=payload.dose_amount,
        dose_unit=payload.dose_unit.strip(),
        instructions=payload.instructions,
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_by_user_id=user.id,
    )
    db.add(medication)
    await db.flush()
    schedule = MedicationSchedule(
        medication_id=medication.id,
        time_of_day=payload.time_of_day,
        days_of_week=payload.days_of_week,
        timezone=payload.timezone,
        reminder_offsets_minutes=payload.reminder_offsets_minutes,
        escalation_after_minutes=payload.escalation_after_minutes,
        assigned_caregiver_user_id=payload.assigned_caregiver_user_id,
    )
    db.add(schedule)
    await db.flush()
    schedule.medication = medication
    add_audit(
        db,
        group_id=access.context.group_id,
        actor_user_id=user.id,
        action="MEDICATION_SCHEDULE_CREATED",
        entity_type="medication_schedule",
        entity_id=schedule.id,
        details={
            "elder_id": str(access.elder.id), "medication_name": medication.name,
            **({"idempotency_key": idempotency_key, "request_fingerprint": fingerprint} if idempotency_key else {}),
        },
    )
    await db.commit()
    return schedule_out(schedule)


async def get_schedule_for_group(
    schedule_id: uuid.UUID, group_id: uuid.UUID, db: DbSession, *, lock: bool = False
) -> MedicationSchedule:
    statement = (
        select(MedicationSchedule)
        .options(selectinload(MedicationSchedule.medication))
        .join(Medication, Medication.id == MedicationSchedule.medication_id)
        .join(ElderProfile, ElderProfile.id == Medication.elder_id)
        .where(MedicationSchedule.id == schedule_id, ElderProfile.group_id == group_id)
    )
    if lock:
        statement = statement.with_for_update(of=MedicationSchedule).execution_options(populate_existing=True)
    schedule = await db.scalar(statement)
    if not schedule:
        raise not_found("Lịch thuốc")
    return schedule


@router.patch("/medication-schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    context: GroupCtx,
    user: CurrentUser,
    db: DbSession,
) -> ScheduleOut:
    if context.role != GroupRole.OWNER:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được sửa lịch thuốc")
    schedule = await get_schedule_for_group(schedule_id, context.group_id, db)
    medication = schedule.medication
    elder = await db.get(ElderProfile, medication.elder_id)
    if not elder.is_active:
        raise AppError(409, "ELDER_INACTIVE", "Hồ sơ đã ngừng theo dõi")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return schedule_out(schedule)
    if not schedule.is_active or not medication.is_active:
        raise AppError(409, "SCHEDULE_INACTIVE", "Lịch đã ngừng và không thể chỉnh sửa")
    await validate_assigned_caregiver(
        db,
        elder_id=medication.elder_id,
        group_id=context.group_id,
        caregiver_user_id=values.get("assigned_caregiver_user_id", schedule.assigned_caregiver_user_id),
    )
    # Membership precedes schedule locks, matching caregiver removal. Refresh
    # after acquiring the schedule lock so a concurrent edit cannot fork it.
    schedule = await get_schedule_for_group(schedule_id, context.group_id, db, lock=True)
    medication = schedule.medication
    if not schedule.is_active or not medication.is_active:
        raise AppError(409, "SCHEDULE_INACTIVE", "Lịch đã ngừng và không thể chỉnh sửa")

    medication_values = {
        "elder_id": medication.elder_id,
        "name": values.get("medication_name", medication.name),
        "dose_amount": values.get("dose_amount", medication.dose_amount),
        "dose_unit": values.get("dose_unit", medication.dose_unit),
        "instructions": values.get("instructions", medication.instructions),
        "start_date": medication.start_date,
        "end_date": values.get("end_date", medication.end_date),
        "created_by_user_id": user.id,
    }
    schedule_values = {
        "time_of_day": values.get("time_of_day", schedule.time_of_day),
        "days_of_week": values.get("days_of_week", schedule.days_of_week),
        "timezone": values.get("timezone", schedule.timezone),
        "reminder_offsets_minutes": values.get(
            "reminder_offsets_minutes", schedule.reminder_offsets_minutes
        ),
        "escalation_after_minutes": values.get(
            "escalation_after_minutes", schedule.escalation_after_minutes
        ),
        "assigned_caregiver_user_id": values.get(
            "assigned_caregiver_user_id", schedule.assigned_caregiver_user_id
        ),
    }
    if (
        schedule_values["escalation_after_minutes"]
        <= schedule_values["reminder_offsets_minutes"][-1]
    ):
        raise AppError(422, "INVALID_REMINDER_TIMING", "Cảnh báo phải sau lần nhắc cuối")
    if (
        medication_values["end_date"]
        and medication_values["end_date"] < medication_values["start_date"]
    ):
        raise AppError(422, "INVALID_DATE_RANGE", "Ngày kết thúc không được trước ngày bắt đầu")

    # Version the schedule so historic occurrences retain the exact medicine
    # name, dose and instructions that applied when they were created.
    replacement_medication = Medication(**medication_values)
    db.add(replacement_medication)
    await db.flush()
    replacement_schedule = MedicationSchedule(
        medication_id=replacement_medication.id,
        **schedule_values,
    )
    db.add(replacement_schedule)
    await db.flush()
    replacement_schedule.medication = replacement_medication
    schedule.is_active = False
    medication.is_active = False

    now = datetime.now(UTC)
    effective_from = now
    previous_version_audit = await db.scalar(select(AuditLog).where(
        AuditLog.action == "MEDICATION_SCHEDULE_UPDATED",
        AuditLog.entity_id == str(schedule.id),
    ))
    if previous_version_audit and previous_version_audit.details.get("effective_from"):
        effective_from = max(effective_from, datetime.fromisoformat(previous_version_audit.details["effective_from"]))
    # One schedule version represents one daily administration. Once today's
    # original dose has become due, editing its hour must not create a second dose
    # for the same day; preserve that old dose and apply the change next day.
    timezone = ZoneInfo(replacement_schedule.timezone)
    local_day = now.astimezone(timezone).date()
    day_start = datetime.combine(local_day, time.min, timezone).astimezone(UTC)
    day_end = datetime.combine(local_day + timedelta(days=1), time.min, timezone).astimezone(UTC)
    already_started = await db.scalar(select(DoseOccurrence.id).where(
        DoseOccurrence.schedule_id == schedule.id,
        DoseOccurrence.scheduled_for >= day_start,
        DoseOccurrence.scheduled_for < day_end,
        DoseOccurrence.scheduled_for <= now,
        DoseOccurrence.status != DoseStatus.CANCELLED,
    ).limit(1))
    if already_started:
        effective_from = max(effective_from, day_end)

    # Future occurrences are derived data. Remove them so the worker creates
    # the replacement schedule at the correct future times.
    await db.execute(
        delete(DoseOccurrence)
        .where(
            DoseOccurrence.schedule_id == schedule.id,
            DoseOccurrence.scheduled_for > now,
            DoseOccurrence.status.in_([DoseStatus.SCHEDULED, DoseStatus.DUE]),
        )
    )
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="MEDICATION_SCHEDULE_UPDATED",
        entity_type="medication_schedule",
        entity_id=replacement_schedule.id,
        details={
            "previous_schedule_id": str(schedule.id),
            "changed_fields": sorted(values),
            "effective_from": effective_from.isoformat(),
        },
    )
    await db.commit()
    return schedule_out(replacement_schedule)


@router.delete("/medication-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_schedule(
    schedule_id: uuid.UUID,
    context: GroupCtx,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    if context.role != GroupRole.OWNER:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được ngừng lịch thuốc")
    schedule = await get_schedule_for_group(schedule_id, context.group_id, db, lock=True)
    schedule.is_active = False
    schedule.medication.is_active = False
    version_audits = (await db.scalars(select(AuditLog).where(
        AuditLog.group_id == context.group_id,
        AuditLog.action == "MEDICATION_SCHEDULE_UPDATED",
    ))).all()
    predecessors = {item.entity_id: item.details.get("previous_schedule_id") for item in version_audits}
    lineage_ids = {schedule.id}
    predecessor = predecessors.get(str(schedule.id))
    while predecessor:
        previous_id = uuid.UUID(predecessor)
        if previous_id in lineage_ids:
            break
        lineage_ids.add(previous_id)
        predecessor = predecessors.get(predecessor)
    actionable = [DoseStatus.SCHEDULED, DoseStatus.DUE, DoseStatus.UNCONFIRMED]
    occurrence_ids = select(DoseOccurrence.id).where(
        DoseOccurrence.schedule_id.in_(lineage_ids),
        DoseOccurrence.status.in_(actionable),
    )
    await db.execute(
        update(NotificationAttempt)
        .where(
            NotificationAttempt.occurrence_id.in_(occurrence_ids),
            NotificationAttempt.delivered.is_(False),
        )
        .values(
            delivery_attempt_count=3,
            next_attempt_at=None,
            error="Medication schedule stopped",
        )
    )
    await db.execute(
        update(DoseOccurrence)
        .where(
            DoseOccurrence.schedule_id.in_(lineage_ids),
            DoseOccurrence.status.in_(actionable),
        )
        .values(status=DoseStatus.CANCELLED, next_action_at=None)
    )
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="MEDICATION_SCHEDULE_STOPPED",
        entity_type="medication_schedule",
        entity_id=schedule.id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
