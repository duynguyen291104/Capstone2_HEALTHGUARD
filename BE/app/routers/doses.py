import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.audit import add_audit
from app.config import get_settings
from app.dependencies import CurrentUser, DbSession, GroupCtx
from app.errors import AppError, not_found
from app.models import (
    CaregiverAssignment,
    DoseOccurrence,
    DoseResponse,
    DoseResponseType,
    DoseStatus,
    ElderProfile,
    GroupRole,
    Medication,
    MedicationSchedule,
    NotificationAttempt,
)
from app.schemas import DoseOccurrenceOut, DoseResponseCreate, DoseResponseOut


router = APIRouter(prefix="/dose-occurrences", tags=["dose-occurrences"])


def occurrence_out(occurrence: DoseOccurrence, *, can_respond: bool) -> DoseOccurrenceOut:
    schedule = occurrence.schedule
    medication = schedule.medication
    elder = medication.elder
    return DoseOccurrenceOut(
        id=occurrence.id,
        elder_id=elder.id,
        elder_name=elder.full_name,
        schedule_id=schedule.id,
        medication_name=medication.name,
        dose_amount=medication.dose_amount,
        dose_unit=medication.dose_unit,
        instructions=medication.instructions,
        scheduled_for=occurrence.scheduled_for,
        status=occurrence.status,
        reminder_count=occurrence.reminder_count,
        can_respond=can_respond,
        response=DoseResponseOut.model_validate(occurrence.response)
        if occurrence.response
        else None,
    )


def occurrence_load_options():
    return (
        selectinload(DoseOccurrence.schedule)
        .selectinload(MedicationSchedule.medication)
        .selectinload(Medication.elder),
        selectinload(DoseOccurrence.response),
    )


@router.get("", response_model=list[DoseOccurrenceOut])
async def list_occurrences(
    context: GroupCtx,
    user: CurrentUser,
    db: DbSession,
    target_date: date | None = Query(default=None, alias="date"),
) -> list[DoseOccurrenceOut]:
    timezone = ZoneInfo(get_settings().default_timezone)
    day = target_date or datetime.now(timezone).date()
    start = datetime.combine(day, time.min, timezone).astimezone(UTC)
    end = datetime.combine(day, time.max, timezone).astimezone(UTC)

    statement = (
        select(DoseOccurrence)
        .options(*occurrence_load_options())
        .join(MedicationSchedule, MedicationSchedule.id == DoseOccurrence.schedule_id)
        .join(Medication, Medication.id == MedicationSchedule.medication_id)
        .join(ElderProfile, ElderProfile.id == Medication.elder_id)
        .where(
            ElderProfile.group_id == context.group_id,
            DoseOccurrence.scheduled_for >= start,
            DoseOccurrence.scheduled_for <= end,
        )
    )
    if context.role == GroupRole.CAREGIVER:
        statement = statement.join(
            CaregiverAssignment,
            CaregiverAssignment.elder_id == ElderProfile.id,
        ).where(
            CaregiverAssignment.caregiver_user_id == user.id,
            CaregiverAssignment.can_view_medications.is_(True),
        )
    occurrences = (await db.scalars(statement.order_by(DoseOccurrence.scheduled_for))).unique().all()
    if context.role == GroupRole.OWNER:
        return [occurrence_out(item, can_respond=True) for item in occurrences]

    elder_ids = {item.schedule.medication.elder_id for item in occurrences}
    assignments = (
        await db.scalars(
            select(CaregiverAssignment).where(
                CaregiverAssignment.elder_id.in_(elder_ids),
                CaregiverAssignment.caregiver_user_id == user.id,
            )
        )
    ).all() if elder_ids else []
    permissions = {item.elder_id: item for item in assignments}
    return [
        occurrence_out(
            item,
            can_respond=bool(
                permissions.get(item.schedule.medication.elder_id)
                and permissions[item.schedule.medication.elder_id].can_view_medications
                and permissions[item.schedule.medication.elder_id].can_confirm_doses
                and (
                    item.schedule.assigned_caregiver_user_id is None
                    or item.schedule.assigned_caregiver_user_id == user.id
                )
            ),
        )
        for item in occurrences
    ]


async def get_occurrence_for_group(
    occurrence_id: uuid.UUID,
    group_id: uuid.UUID,
    db: DbSession,
) -> DoseOccurrence:
    occurrence = await db.scalar(
        select(DoseOccurrence)
        .options(*occurrence_load_options())
        .join(MedicationSchedule, MedicationSchedule.id == DoseOccurrence.schedule_id)
        .join(Medication, Medication.id == MedicationSchedule.medication_id)
        .join(ElderProfile, ElderProfile.id == Medication.elder_id)
        .where(DoseOccurrence.id == occurrence_id, ElderProfile.group_id == group_id)
    )
    if not occurrence:
        raise not_found("Lần uống thuốc")
    return occurrence


@router.post("/{occurrence_id}/responses", response_model=DoseOccurrenceOut)
async def respond_to_occurrence(
    occurrence_id: uuid.UUID,
    payload: DoseResponseCreate,
    context: GroupCtx,
    user: CurrentUser,
    db: DbSession,
) -> DoseOccurrenceOut:
    occurrence = await get_occurrence_for_group(occurrence_id, context.group_id, db)
    schedule = occurrence.schedule
    elder = schedule.medication.elder

    if context.role == GroupRole.CAREGIVER:
        assignment = await db.scalar(
            select(CaregiverAssignment).where(
                CaregiverAssignment.elder_id == elder.id,
                CaregiverAssignment.caregiver_user_id == user.id,
                CaregiverAssignment.can_view_medications.is_(True),
                CaregiverAssignment.can_confirm_doses.is_(True),
            )
        )
        if not assignment:
            raise AppError(403, "DOSE_CONFIRMATION_DENIED", "Bạn không có quyền xác nhận lần uống này")
        if (
            schedule.assigned_caregiver_user_id
            and schedule.assigned_caregiver_user_id != user.id
        ):
            raise AppError(403, "DOSE_ASSIGNED_TO_ANOTHER", "Lần uống này được giao cho người khác")

    if occurrence.status == DoseStatus.CANCELLED:
        raise AppError(409, "DOSE_CANCELLED", "Lần uống này đã bị hủy")
    if occurrence.response:
        existing = occurrence.response
        same = (
            existing.responded_by_user_id == user.id
            and existing.response_type == payload.status
            and existing.reason == payload.reason_code
            and existing.notes == payload.note
        )
        if same:
            return occurrence_out(occurrence, can_respond=True)
        raise AppError(409, "DOSE_ALREADY_RESPONDED", "Lần uống này đã được phản hồi")

    now = datetime.now(UTC)
    scheduled_for = occurrence.scheduled_for
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=UTC)
    if occurrence.status == DoseStatus.SCHEDULED and scheduled_for > now:
        raise AppError(409, "DOSE_NOT_DUE", "Chưa đến giờ thực hiện lần uống này")
    if occurrence.status not in {
        DoseStatus.SCHEDULED,
        DoseStatus.DUE,
        DoseStatus.UNCONFIRMED,
    }:
        raise AppError(409, "DOSE_NOT_ACTIONABLE", "Lần uống này không còn chờ phản hồi")

    response = DoseResponse(
        occurrence_id=occurrence.id,
        responded_by_user_id=user.id,
        response_type=payload.status,
        administered_at=(payload.administered_at or now)
        if payload.status == DoseResponseType.ADMINISTERED
        else None,
        reason=payload.reason_code,
        notes=payload.note,
    )
    db.add(response)
    occurrence.response = response
    occurrence.status = (
        DoseStatus.ADMINISTERED
        if payload.status == DoseResponseType.ADMINISTERED
        else DoseStatus.CANNOT_ADMINISTER
    )
    occurrence.next_action_at = None
    await db.execute(
        update(NotificationAttempt)
        .where(
            NotificationAttempt.occurrence_id == occurrence.id,
            NotificationAttempt.delivered.is_(False),
        )
        .values(
            delivery_attempt_count=3,
            next_attempt_at=None,
            error="Dose already responded",
        )
    )
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="DOSE_RESPONSE_RECORDED",
        entity_type="dose_occurrence",
        entity_id=occurrence.id,
        details={"status": payload.status.value, "reason_code": payload.reason_code},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        current = await get_occurrence_for_group(occurrence_id, context.group_id, db)
        if current.response and current.response.responded_by_user_id == user.id:
            return occurrence_out(current, can_respond=True)
        raise AppError(409, "DOSE_ALREADY_RESPONDED", "Lần uống này đã được phản hồi") from None
    await db.refresh(response)
    return occurrence_out(occurrence, can_respond=True)
