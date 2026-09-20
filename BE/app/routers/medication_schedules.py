import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status
from sqlalchemy import select, update
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
    if "assigned_caregiver_user_id" in values:
        await validate_assigned_caregiver(
            db,
            elder_id=medication.elder_id,
            group_id=context.group_id,
            caregiver_user_id=values["assigned_caregiver_user_id"],
        )

    medication_fields = {
        "medication_name": "name",
        "dose_amount": "dose_amount",
        "dose_unit": "dose_unit",
        "instructions": "instructions",
        "end_date": "end_date",
    }
    schedule_fields = {
        "time_of_day",
        "days_of_week",
        "timezone",
        "reminder_offsets_minutes",
        "escalation_after_minutes",
        "assigned_caregiver_user_id",
        "is_active",
    }
    for source, target in medication_fields.items():
        if source in values:
            setattr(medication, target, values[source])
    for field in schedule_fields:
        if field in values:
            setattr(schedule, field, values[field])
    if schedule.escalation_after_minutes <= schedule.reminder_offsets_minutes[-1]:
        raise AppError(422, "INVALID_REMINDER_TIMING", "Cảnh báo phải sau lần nhắc cuối")

    await db.execute(
        update(DoseOccurrence)
        .where(
            DoseOccurrence.schedule_id == schedule.id,
            DoseOccurrence.scheduled_for > datetime.now(UTC),
            DoseOccurrence.status.in_([DoseStatus.SCHEDULED, DoseStatus.DUE]),
        )
        .values(status=DoseStatus.CANCELLED, next_action_at=None)
    )
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="MEDICATION_SCHEDULE_UPDATED",
        entity_type="medication_schedule",
        entity_id=schedule.id,
        details={"changed_fields": sorted(values)},
    )
    await db.commit()
    await db.refresh(schedule)
    schedule.medication = medication
    return schedule_out(schedule)


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
    await db.execute(
        update(DoseOccurrence)
        .where(
            DoseOccurrence.schedule_id == schedule.id,
            DoseOccurrence.scheduled_for > datetime.now(UTC),
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

