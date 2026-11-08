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
from app.schemas import (
    CareGroupCreate,
    GroupSummary,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    MemberOut,
    RoomCodeCreate,
    RoomCodeOut,
    RoomJoin,
)
from app.security import generate_invitation_token, hash_invitation_token, hash_password, verify_password
from starlette.concurrency import run_in_threadpool


router = APIRouter(prefix="/care-groups", tags=["care-groups"])


@router.post("", response_model=GroupSummary, status_code=status.HTTP_201_CREATED)
async def create_care_group(
    payload: CareGroupCreate,
    user: CurrentUser,
    db: DbSession,
) -> GroupSummary:
    # Serialize group creation and invitation acceptance for the same account.
    # The current scope permits one care group per account.
    await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    existing_membership = await db.scalar(
        select(CareGroupMember.id).where(CareGroupMember.user_id == user.id)
    )
    if existing_membership:
        raise AppError(409, "GROUP_ALREADY_EXISTS", "Tài khoản đã thuộc một nhóm chăm sóc")
    group = CareGroup(name=payload.name, created_by_user_id=user.id)
    db.add(group)
    await db.flush()
    db.add(CareGroupMember(group_id=group.id, user_id=user.id, role=GroupRole.OWNER))
    add_audit(
        db,
        group_id=group.id,
        actor_user_id=user.id,
        action="CARE_GROUP_CREATED",
        entity_type="care_group",
        entity_id=group.id,
    )
    await db.commit()
    return GroupSummary(id=group.id, name=group.name, role=GroupRole.OWNER)


@router.get("/current/members", response_model=list[MemberOut])
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
            telegram_linked=bool(user.telegram_chat_id),
        )
        for member, user in rows
    ]


@router.post("/current/room-code", response_model=RoomCodeOut)
async def issue_room_code(payload: RoomCodeCreate, context: OwnerCtx, user: CurrentUser, db: DbSession, response: Response) -> RoomCodeOut:
    password_hash = await run_in_threadpool(hash_password, payload.password) if payload.password else None
    group = await db.scalar(select(CareGroup).where(CareGroup.id == context.group_id).with_for_update())
    if group is None:
        raise not_found("Nhóm chăm sóc")
    code = generate_invitation_token()
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    group.join_code_hash = hash_invitation_token(code)
    group.join_password_hash = password_hash
    group.join_expires_at = expires_at
    group.join_failed_attempts = 0
    add_audit(db, group_id=group.id, actor_user_id=user.id, action="ROOM_CODE_ISSUED", entity_type="care_group", entity_id=group.id)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return RoomCodeOut(code=code, expires_at=expires_at)


@router.delete("/current/room-code", status_code=204)
async def revoke_room_code(context: OwnerCtx, user: CurrentUser, db: DbSession) -> Response:
    group = await db.scalar(select(CareGroup).where(CareGroup.id == context.group_id).with_for_update())
    if group is None:
        raise not_found("Nhóm chăm sóc")
    group.join_code_hash = group.join_password_hash = None
    group.join_expires_at = group.join_failed_attempts = None
    add_audit(db, group_id=group.id, actor_user_id=user.id, action="ROOM_CODE_REVOKED", entity_type="care_group", entity_id=group.id)
    await db.commit()
    return Response(status_code=204)


@router.post("/join", response_model=GroupSummary)
async def join_room(payload: RoomJoin, user: CurrentUser, db: DbSession) -> GroupSummary:
    group = await db.scalar(select(CareGroup).where(CareGroup.join_code_hash == hash_invitation_token(payload.code)).with_for_update())
    if group is None or group.join_expires_at is None:
        raise AppError(404, "ROOM_CODE_INVALID", "Mã phòng không hợp lệ hoặc đã hết hạn")
    expires = group.join_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        raise AppError(404, "ROOM_CODE_INVALID", "Mã phòng không hợp lệ hoặc đã hết hạn")
    await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    if await db.scalar(select(CareGroupMember.id).where(CareGroupMember.user_id == user.id)):
        raise AppError(409, "GROUP_ALREADY_EXISTS", "Tài khoản đã thuộc một nhóm chăm sóc")
    if group.join_password_hash:
        if not payload.password:
            raise AppError(403, "ROOM_PASSWORD_REQUIRED", "Phòng này cần mật khẩu do chủ nhóm cung cấp")
        failures = group.join_failed_attempts or 0
        if failures >= 5:
            raise AppError(429, "ROOM_CODE_LOCKED", "Mã phòng đã khóa vì nhập sai nhiều lần. Hãy xin mã mới.")
        if not await run_in_threadpool(verify_password, payload.password, group.join_password_hash):
            group.join_failed_attempts = failures + 1
            await db.commit()
            raise AppError(403, "ROOM_PASSWORD_INCORRECT", "Mật khẩu phòng không đúng")
    db.add(CareGroupMember(group_id=group.id, user_id=user.id, role=GroupRole.CAREGIVER))
    add_audit(db, group_id=group.id, actor_user_id=user.id, action="CARE_GROUP_JOINED_BY_CODE", entity_type="care_group", entity_id=group.id)
    result = GroupSummary(id=group.id, name=group.name, role=GroupRole.CAREGIVER)
    await db.commit()
    return result


@router.post(
    "/current/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    payload: InvitationCreate,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> InvitationCreated:
    # Serialize reissues in this group before searching for outstanding tokens;
    # otherwise two concurrent creates can both leave a usable invitation.
    await db.scalar(select(CareGroup.id).where(CareGroup.id == context.group_id).with_for_update())
    email = str(payload.email).strip().lower()
    invited_user = await db.scalar(select(User.id).where(User.email == email, User.is_active.is_(True)))
    if invited_user is None:
        raise AppError(404, "INVITED_ACCOUNT_NOT_FOUND", "Email này không có tài khoản trong HealthGuard. Hãy yêu cầu người chăm sóc đăng ký trước.")
    existing_member = await db.scalar(
        select(CareGroupMember.group_id).where(CareGroupMember.user_id == invited_user)
    )
    if existing_member:
        raise AppError(409, "ALREADY_A_MEMBER", "Tài khoản này đã thuộc một nhóm chăm sóc")

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


@router.get("/current/invitations", response_model=list[InvitationOut])
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


@router.delete(
    "/current/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    context: OwnerCtx,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    await db.scalar(select(CareGroup.id).where(CareGroup.id == context.group_id).with_for_update())
    invitation = await db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.group_id == context.group_id,
        )
        .with_for_update()
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


@router.delete("/current/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
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
        .with_for_update()
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
