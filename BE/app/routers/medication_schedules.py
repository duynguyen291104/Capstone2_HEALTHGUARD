import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from app.audit import add_audit
from app.dependencies import CurrentUser, DbSession, ElderAccessDep, GroupCtx
from app.errors import AppError, not_found
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
    access: ElderAccessDep,
    user: CurrentUser,
    db: DbSession,
) -> ScheduleOut:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được tạo lịch thuốc")
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
        details={"elder_id": str(access.elder.id), "medication_name": medication.name},
    )
    await db.commit()
    return schedule_out(schedule)


async def get_schedule_for_group(
    schedule_id: uuid.UUID, group_id: uuid.UUID, db: DbSession
) -> MedicationSchedule:
    schedule = await db.scalar(
        select(MedicationSchedule)
        .options(selectinload(MedicationSchedule.medication))
        .join(Medication, Medication.id == MedicationSchedule.medication_id)
        .join(ElderProfile, ElderProfile.id == Medication.elder_id)
        .where(MedicationSchedule.id == schedule_id, ElderProfile.group_id == group_id)
    )
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
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return schedule_out(schedule)
    if not schedule.is_active or not medication.is_active:
        raise AppError(409, "SCHEDULE_INACTIVE", "Lịch đã ngừng và không thể chỉnh sửa")
    if "assigned_caregiver_user_id" in values:
        await validate_assigned_caregiver(
            db,
            elder_id=medication.elder_id,
            group_id=context.group_id,
            caregiver_user_id=values["assigned_caregiver_user_id"],
        )

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

    # Future occurrences are derived data. Remove them so the worker creates
    # the replacement schedule at the correct future times.
    await db.execute(
        delete(DoseOccurrence)
        .where(
            DoseOccurrence.schedule_id == schedule.id,
            DoseOccurrence.scheduled_for > datetime.now(UTC),
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
    schedule = await get_schedule_for_group(schedule_id, context.group_id, db)
    schedule.is_active = False
    schedule.medication.is_active = False
    occurrence_ids = select(DoseOccurrence.id).where(
        DoseOccurrence.schedule_id == schedule.id,
        DoseOccurrence.status.in_([DoseStatus.SCHEDULED, DoseStatus.DUE]),
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
            DoseOccurrence.schedule_id == schedule.id,
            DoseOccurrence.status.in_([DoseStatus.SCHEDULED, DoseStatus.DUE]),
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
