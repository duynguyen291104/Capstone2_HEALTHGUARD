import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response, status
from sqlalchemy import delete, select, update

from app.audit import add_audit
from app.config import get_settings
from app.dependencies import CurrentUser, DbSession, OwnerCtx
from app.errors import AppError, not_found
from app.models import (
    CareGroup,
    CareGroupMember,
    CaregiverAssignment,
    ElderProfile,
    GroupRole,
    Invitation,
    Medication,
    MedicationSchedule,
    User,
)
from app.schemas import InvitationCreate, InvitationCreated, InvitationOut, MemberOut
from app.security import generate_invitation_token, hash_invitation_token


router = APIRouter(prefix="/care-groups/current", tags=["care-groups"])


@router.get("/members", response_model=list[MemberOut])
async def list_members(context: OwnerCtx, db: DbSession) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(CareGroupMember, User)
            .join(User, User.id == CareGroupMember.user_id)
            .where(CareGroupMember.group_id == context.group_id)
            .order_by(CareGroupMember.joined_at)
        )
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]


@router.post("/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InvitationCreate,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> InvitationCreated:
    email = str(payload.email).strip().lower()
    existing_member = await db.scalar(
        select(CareGroupMember.id)
        .join(User, User.id == CareGroupMember.user_id)
        .where(CareGroupMember.group_id == context.group_id, User.email == email)
    )
    if existing_member:
        raise AppError(409, "ALREADY_A_MEMBER", "Email này đã thuộc nhóm chăm sóc")

    now = datetime.now(UTC)
    outstanding = (
        await db.scalars(
            select(Invitation).where(
                Invitation.group_id == context.group_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
    ).all()
    for invitation in outstanding:
        invitation.revoked_at = now

    raw_token = generate_invitation_token()
    invitation = Invitation(
        group_id=context.group_id,
        email=email,
        role=GroupRole.CAREGIVER,
        token_hash=hash_invitation_token(raw_token),
        expires_at=now + timedelta(hours=payload.expires_in_hours),
        invited_by_user_id=user.id,
    )
    db.add(invitation)
    await db.flush()
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="INVITATION_CREATED",
        entity_type="invitation",
        entity_id=invitation.id,
        details={"email": email, "expires_at": invitation.expires_at.isoformat()},
    )
    await db.commit()
    url = f"{get_settings().frontend_base_url.rstrip('/')}/tham-gia?token={raw_token}"
    return InvitationCreated(
        id=invitation.id,
        email=email,
        expires_at=invitation.expires_at,
        invitation_token=raw_token,
        invitation_url=url,
    )


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(context: OwnerCtx, db: DbSession) -> list[Invitation]:
    return list(
        (
            await db.scalars(
                select(Invitation)
                .where(Invitation.group_id == context.group_id)
                .order_by(Invitation.created_at.desc())
            )
        ).all()
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    invitation = await db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.group_id == context.group_id,
        )
    )
    if not invitation:
        raise not_found("Lời mời")
    if invitation.accepted_at:
        raise AppError(409, "INVITATION_ALREADY_ACCEPTED", "Lời mời đã được chấp nhận")
    if invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC)
        add_audit(
            db,
            group_id=context.group_id,
            actor_user_id=user.id,
            action="INVITATION_REVOKED",
            entity_type="invitation",
            entity_id=invitation.id,
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_user_id: uuid.UUID,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    membership = await db.scalar(
        select(CareGroupMember).where(
            CareGroupMember.group_id == context.group_id,
            CareGroupMember.user_id == member_user_id,
        )
    )
    if not membership:
        raise not_found("Thành viên")
    if membership.role == GroupRole.OWNER:
        raise AppError(409, "OWNER_CANNOT_BE_REMOVED", "Không thể thu hồi chủ nhóm")

    elder_ids = select(ElderProfile.id).where(ElderProfile.group_id == context.group_id)
    medication_ids = select(Medication.id).where(Medication.elder_id.in_(elder_ids))
    await db.execute(
        update(MedicationSchedule)
        .where(
            MedicationSchedule.medication_id.in_(medication_ids),
            MedicationSchedule.assigned_caregiver_user_id == member_user_id,
        )
        .values(assigned_caregiver_user_id=None)
    )
    await db.execute(
        delete(CaregiverAssignment).where(
            CaregiverAssignment.caregiver_user_id == member_user_id,
            CaregiverAssignment.elder_id.in_(elder_ids),
        )
    )
    await db.delete(membership)
    add_audit(
        db,
        group_id=context.group_id,
        actor_user_id=user.id,
        action="MEMBER_REMOVED",
        entity_type="user",
        entity_id=member_user_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
