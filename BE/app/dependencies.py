from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.errors import AppError, forbidden, not_found
from app.models import (
    AuthSession,
    CareGroupMember,
    CaregiverAssignment,
    ElderProfile,
    GroupRole,
    User,
)
from app.security import decode_access_token


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    access_cookie: Annotated[str | None, Cookie(alias=get_settings().cookie_name)] = None,
) -> User:
    if not access_cookie:
        raise AppError(401, "NOT_AUTHENTICATED", "Bạn cần đăng nhập")
    try:
        user_id, session_id = decode_access_token(access_cookie)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise AppError(401, "INVALID_SESSION", "Phiên đăng nhập không hợp lệ") from None

    result = await db.execute(
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.id == user_id, AuthSession.id == session_id)
    )
    row = result.first()
    if not row:
        raise AppError(401, "INVALID_SESSION", "Phiên đăng nhập không hợp lệ")
    user, auth_session = row
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if auth_session.revoked_at is not None or expires_at <= datetime.now(UTC) or not user.is_active:
        raise AppError(401, "INVALID_SESSION", "Phiên đăng nhập đã hết hạn")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass
class GroupContext:
    membership: CareGroupMember

    @property
    def group_id(self) -> uuid.UUID:
        return self.membership.group_id

    @property
    def role(self) -> GroupRole:
        return self.membership.role


async def get_group_context(
    db: DbSession,
    user: CurrentUser,
    requested_group_id: Annotated[uuid.UUID | None, Header(alias="X-Care-Group-ID")] = None,
) -> GroupContext:
    statement = select(CareGroupMember).where(CareGroupMember.user_id == user.id)
    if requested_group_id:
        statement = statement.where(CareGroupMember.group_id == requested_group_id)
    statement = statement.order_by(CareGroupMember.joined_at)
    membership = (await db.scalars(statement)).first()
    if not membership:
        raise forbidden("Bạn chưa thuộc nhóm chăm sóc này")
    return GroupContext(membership)


GroupCtx = Annotated[GroupContext, Depends(get_group_context)]


async def require_owner(context: GroupCtx) -> GroupContext:
    if context.role != GroupRole.OWNER:
        raise forbidden("Chỉ chủ nhóm chăm sóc mới được thực hiện thao tác này")
    return context


OwnerCtx = Annotated[GroupContext, Depends(require_owner)]


@dataclass
class ElderAccess:
    elder: ElderProfile
    context: GroupContext
    assignment: CaregiverAssignment | None

    @property
    def is_owner(self) -> bool:
        return self.context.role == GroupRole.OWNER


async def get_elder_access(
    elder_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    context: GroupCtx,
) -> ElderAccess:
    elder = await db.get(ElderProfile, elder_id)
    if not elder or elder.group_id != context.group_id:
        raise not_found("Hồ sơ người cao tuổi")
    assignment = None
    if context.role == GroupRole.CAREGIVER:
        assignment = await db.scalar(
            select(CaregiverAssignment).where(
                CaregiverAssignment.elder_id == elder.id,
                CaregiverAssignment.caregiver_user_id == user.id,
            )
        )
        if not assignment:
            raise not_found("Hồ sơ người cao tuổi")
    return ElderAccess(elder, context, assignment)


ElderAccessDep = Annotated[ElderAccess, Depends(get_elder_access)]
