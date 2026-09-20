import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select, update

from app.audit import add_audit
from app.dependencies import (
    CurrentUser,
    DbSession,
    ElderAccessDep,
    GroupCtx,
    OwnerCtx,
)
from app.errors import AppError, not_found
from app.models import (
    CareGroupMember,
    CaregiverAssignment,
    ElderProfile,
    GroupRole,
    Medication,
    MedicationSchedule,
)
from app.schemas import AssignmentOut, AssignmentUpsert, ElderCreate, ElderOut, ElderUpdate


router = APIRouter(prefix="/elders", tags=["elders"])


def elder_out(elder: ElderProfile, *, can_view_diagnoses: bool) -> ElderOut:
    return ElderOut(
        id=elder.id,
        group_id=elder.group_id,
        full_name=elder.full_name,
        date_of_birth=elder.date_of_birth,
        sex=elder.sex,
        height_cm=elder.height_cm,
        weight_kg=elder.weight_kg,
        diagnosed_conditions=elder.diagnoses if can_view_diagnoses else None,
        current_medications_note=elder.current_medications_note if can_view_diagnoses else None,
        mobility_level=elder.mobility_level,
        sleep_habits=elder.sleep_habits,
        emergency_contact_name=elder.emergency_contact_name,
        emergency_contact_phone=elder.emergency_contact_phone,
        is_active=elder.is_active,
        created_at=elder.created_at,
        updated_at=elder.updated_at,
    )


@router.get("", response_model=list[ElderOut])
async def list_elders(
    context: GroupCtx,
    user: CurrentUser,
    db: DbSession,
) -> list[ElderOut]:
    if context.role == GroupRole.OWNER:
        elders = (
            await db.scalars(
                select(ElderProfile)
                .where(ElderProfile.group_id == context.group_id)
                .order_by(ElderProfile.created_at)
            )
        ).all()
        return [elder_out(item, can_view_diagnoses=True) for item in elders]

    rows = (
        await db.execute(
            select(ElderProfile, CaregiverAssignment)
            .join(CaregiverAssignment, CaregiverAssignment.elder_id == ElderProfile.id)
            .where(
                ElderProfile.group_id == context.group_id,
                CaregiverAssignment.caregiver_user_id == user.id,
            )
            .order_by(ElderProfile.created_at)
        )
    ).all()
    return [
        elder_out(elder, can_view_diagnoses=assignment.can_view_diagnoses)
        for elder, assignment in rows
    ]


@router.post("", response_model=ElderOut, status_code=status.HTTP_201_CREATED)
async def create_elder(
    payload: ElderCreate,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> ElderOut:
    elder = ElderProfile(
        group_id=context.group_id,
        full_name=payload.full_name.strip(),
        date_of_birth=payload.date_of_birth,
        sex=payload.sex,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        diagnoses=payload.diagnosed_conditions,
        current_medications_note=payload.current_medications_note,
        mobility_level=payload.mobility_level,
        sleep_habits=payload.sleep_habits,
        emergency_contact_name=payload.emergency_contact_name,
        emergency_contact_phone=payload.emergency_contact_phone,
    )
    db.add(elder)
    await db.flush()
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="ELDER_CREATED",
        entity_type="elder_profile",
        entity_id=elder.id,
    )
    await db.commit()
    await db.refresh(elder)
    return elder_out(elder, can_view_diagnoses=True)


@router.get("/{elder_id}", response_model=ElderOut)
async def get_elder(access: ElderAccessDep) -> ElderOut:
    can_view = access.is_owner or bool(access.assignment and access.assignment.can_view_diagnoses)
    return elder_out(access.elder, can_view_diagnoses=can_view)


@router.patch("/{elder_id}", response_model=ElderOut)
async def update_elder(
    payload: ElderUpdate,
    access: ElderAccessDep,
    user: CurrentUser,
    db: DbSession,
) -> ElderOut:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được sửa hồ sơ")
    values = payload.model_dump(exclude_unset=True)
    if "diagnosed_conditions" in values:
        values["diagnoses"] = values.pop("diagnosed_conditions")
    for field, value in values.items():
        setattr(access.elder, field, value)
    add_audit(
        db,
        group_id=access.context.group_id,
        actor_user_id=user.id,
        action="ELDER_UPDATED",
        entity_type="elder_profile",
        entity_id=access.elder.id,
        details={"changed_fields": sorted(values)},
    )
    await db.commit()
    await db.refresh(access.elder)
    return elder_out(access.elder, can_view_diagnoses=True)


@router.put("/{elder_id}/caregivers/{caregiver_user_id}", response_model=AssignmentOut)
async def assign_caregiver(
    caregiver_user_id: uuid.UUID,
    payload: AssignmentUpsert,
    access: ElderAccessDep,
    user: CurrentUser,
    db: DbSession,
) -> AssignmentOut:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được phân công người chăm sóc")
    membership = await db.scalar(
        select(CareGroupMember).where(
            CareGroupMember.group_id == access.context.group_id,
            CareGroupMember.user_id == caregiver_user_id,
            CareGroupMember.role == GroupRole.CAREGIVER,
        )
    )
    if not membership:
        raise not_found("Người chăm sóc")
    assignment = await db.scalar(
        select(CaregiverAssignment).where(
            CaregiverAssignment.elder_id == access.elder.id,
            CaregiverAssignment.caregiver_user_id == caregiver_user_id,
        )
    )
    if not assignment:
        assignment = CaregiverAssignment(
            elder_id=access.elder.id,
            caregiver_user_id=caregiver_user_id,
            assigned_by_user_id=user.id,
        )
        db.add(assignment)
    assignment.can_view_medications = payload.can_view_medications
    assignment.can_confirm_doses = payload.can_confirm_doses
    assignment.can_view_diagnoses = payload.can_view_diagnoses
    if not payload.can_confirm_doses:
        medication_ids = select(Medication.id).where(Medication.elder_id == access.elder.id)
        await db.execute(
            update(MedicationSchedule)
            .where(
                MedicationSchedule.medication_id.in_(medication_ids),
                MedicationSchedule.assigned_caregiver_user_id == caregiver_user_id,
            )
            .values(assigned_caregiver_user_id=None)
        )
    add_audit(
        db,
        group_id=access.context.group_id,
        actor_user_id=user.id,
        action="CAREGIVER_ASSIGNED",
        entity_type="caregiver_assignment",
        entity_id=assignment.id,
        details={"elder_id": str(access.elder.id), "caregiver_user_id": str(caregiver_user_id)},
    )
    await db.commit()
    await db.refresh(assignment)
    return AssignmentOut.model_validate(assignment)


@router.get("/{elder_id}/caregivers", response_model=list[AssignmentOut])
async def list_assignments(access: ElderAccessDep, db: DbSession) -> list[CaregiverAssignment]:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được xem phân công")
    return list(
        (
            await db.scalars(
                select(CaregiverAssignment)
                .where(CaregiverAssignment.elder_id == access.elder.id)
                .order_by(CaregiverAssignment.created_at)
            )
        ).all()
    )


@router.delete(
    "/{elder_id}/caregivers/{caregiver_user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def unassign_caregiver(
    caregiver_user_id: uuid.UUID,
    access: ElderAccessDep,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được hủy phân công")
    assignment = await db.scalar(
        select(CaregiverAssignment).where(
            CaregiverAssignment.elder_id == access.elder.id,
            CaregiverAssignment.caregiver_user_id == caregiver_user_id,
        )
    )
    if not assignment:
        raise not_found("Phân công")
    medication_ids = select(Medication.id).where(Medication.elder_id == access.elder.id)
    await db.execute(
        update(MedicationSchedule)
        .where(
            MedicationSchedule.medication_id.in_(medication_ids),
            MedicationSchedule.assigned_caregiver_user_id == caregiver_user_id,
        )
        .values(assigned_caregiver_user_id=None)
    )
    await db.delete(assignment)
    add_audit(
        db,
        group_id=access.context.group_id,
        actor_user_id=user.id,
        action="CAREGIVER_UNASSIGNED",
        entity_type="caregiver_assignment",
        entity_id=assignment.id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
