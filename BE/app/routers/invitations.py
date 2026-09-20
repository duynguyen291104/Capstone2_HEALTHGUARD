from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.audit import add_audit
from app.dependencies import CurrentUser, DbSession
from app.errors import AppError
from app.models import CareGroupMember, Invitation
from app.schemas import InvitationPublic, MessageOut
from app.security import hash_invitation_token


router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.get("/{token}", response_model=InvitationPublic)
async def inspect_invitation(token: str, db: DbSession) -> InvitationPublic:
    invitation = await db.scalar(
        select(Invitation)
        .options(selectinload(Invitation.group))
        .where(Invitation.token_hash == hash_invitation_token(token))
    )
    if not invitation:
        raise AppError(404, "INVITATION_INVALID", "Lời mời không hợp lệ")
    now = datetime.now(UTC)
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    valid = not invitation.accepted_at and not invitation.revoked_at and expires_at > now
    if not valid:
        code = "INVITATION_EXPIRED" if expires_at <= now else "INVITATION_UNAVAILABLE"
        raise AppError(410, code, "Lời mời đã hết hạn, được sử dụng hoặc bị thu hồi")
    return InvitationPublic(
        email=invitation.email,
        care_group_name=invitation.group.name,
        expires_at=invitation.expires_at,
        valid=True,
    )


@router.post("/{token}/accept", response_model=MessageOut)
async def accept_invitation(
    token: str,
    user: CurrentUser,
    db: DbSession,
) -> MessageOut:
    invitation = await db.scalar(
        select(Invitation)
        .where(Invitation.token_hash == hash_invitation_token(token))
        .with_for_update()
    )
    if not invitation:
        raise AppError(404, "INVITATION_INVALID", "Lời mời không hợp lệ")
    now = datetime.now(UTC)
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if invitation.accepted_at or invitation.revoked_at:
        raise AppError(409, "INVITATION_UNAVAILABLE", "Lời mời đã được sử dụng hoặc thu hồi")
    if expires_at <= now:
        raise AppError(410, "INVITATION_EXPIRED", "Lời mời đã hết hạn")
    if invitation.email != user.email:
        raise AppError(403, "INVITATION_EMAIL_MISMATCH", "Email không khớp với lời mời")
    existing = await db.scalar(
        select(CareGroupMember.id).where(
            CareGroupMember.group_id == invitation.group_id,
            CareGroupMember.user_id == user.id,
        )
    )
    if existing:
        raise AppError(409, "ALREADY_A_MEMBER", "Bạn đã thuộc nhóm chăm sóc này")
    db.add(
        CareGroupMember(
            group_id=invitation.group_id,
            user_id=user.id,
            role=invitation.role,
        )
    )
    invitation.accepted_at = now
    add_audit(
        db,
        group_id=invitation.group_id,
        actor_user_id=user.id,
        action="INVITATION_ACCEPTED",
        entity_type="invitation",
        entity_id=invitation.id,
        details={"caregiver_user_id": str(user.id)},
    )
    await db.commit()
    return MessageOut(message="Đã tham gia nhóm chăm sóc")
