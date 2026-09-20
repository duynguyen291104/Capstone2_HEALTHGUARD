from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.audit import add_audit
from app.config import get_settings
from app.dependencies import CurrentUser, DbSession
from app.errors import AppError
from app.models import (
    AuthSession,
    CareGroupMember,
    GroupRole,
    Invitation,
    TelegramLinkCode,
    User,
)
from app.schemas import (
    AccountRegister,
    AuthOut,
    CaregiverRegister,
    GroupSummary,
    LoginRequest,
    MessageOut,
    TelegramLinkOut,
    UserOut,
)
from app.security import (
    create_access_token,
    decode_access_token,
    hash_invitation_token,
    hash_password,
    generate_invitation_token,
    new_session_expiry,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        telegram_linked=bool(user.telegram_chat_id),
    )


async def auth_out(db: DbSession, user: User) -> AuthOut:
    memberships = (
        await db.scalars(
            select(CareGroupMember)
            .options(selectinload(CareGroupMember.group))
            .where(CareGroupMember.user_id == user.id)
            .order_by(CareGroupMember.joined_at)
        )
    ).all()
    groups = [
        GroupSummary(id=item.group_id, name=item.group.name, role=item.role) for item in memberships
    ]
    return AuthOut(
        user=user_out(user),
        groups=groups,
        default_group_id=groups[0].id if groups else None,
    )


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


async def create_login_session(db: DbSession, user: User, response: Response) -> None:
    expires_at = new_session_expiry()
    auth_session = AuthSession(user_id=user.id, expires_at=expires_at)
    db.add(auth_session)
    await db.flush()
    token = create_access_token(user.id, auth_session.id, expires_at)
    set_auth_cookie(response, token)


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register_account(payload: AccountRegister, response: Response, db: DbSession) -> AuthOut:
    email = normalize_email(str(payload.email))
    if await db.scalar(select(User.id).where(User.email == email)):
        raise AppError(409, "EMAIL_ALREADY_EXISTS", "Email này đã được sử dụng")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await create_login_session(db, user, response)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "EMAIL_ALREADY_EXISTS", "Email này đã được sử dụng") from None
    return await auth_out(db, user)


@router.post("/register-caregiver", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register_caregiver(
    payload: CaregiverRegister, response: Response, db: DbSession
) -> AuthOut:
    email = normalize_email(str(payload.email))
    invitation = await db.scalar(
        select(Invitation)
        .where(Invitation.token_hash == hash_invitation_token(payload.invitation_token))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if not invitation:
        raise AppError(400, "INVITATION_INVALID", "Lời mời không hợp lệ")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if invitation.revoked_at or invitation.accepted_at:
        raise AppError(400, "INVITATION_UNAVAILABLE", "Lời mời đã được sử dụng hoặc thu hồi")
    if expires_at <= now:
        raise AppError(400, "INVITATION_EXPIRED", "Lời mời đã hết hạn")
    if invitation.email != email:
        raise AppError(403, "INVITATION_EMAIL_MISMATCH", "Email không khớp với lời mời")
    if await db.scalar(select(User.id).where(User.email == email)):
        raise AppError(409, "EMAIL_ALREADY_EXISTS", "Email này đã được sử dụng")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    db.add(
        CareGroupMember(
            group_id=invitation.group_id,
            user_id=user.id,
            role=GroupRole.CAREGIVER,
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
    await create_login_session(db, user, response)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "REGISTRATION_CONFLICT", "Không thể sử dụng lời mời này") from None
    return await auth_out(db, user)


@router.post("/login", response_model=AuthOut)
async def login(payload: LoginRequest, response: Response, db: DbSession) -> AuthOut:
    user = await db.scalar(select(User).where(User.email == normalize_email(str(payload.email))))
    if not user or not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise AppError(401, "INVALID_CREDENTIALS", "Email hoặc mật khẩu không đúng")
    await create_login_session(db, user, response)
    await db.commit()
    return await auth_out(db, user)


@router.post("/logout", response_model=MessageOut)
async def logout(
    response: Response,
    user: CurrentUser,
    db: DbSession,
    token: Annotated[str | None, Cookie(alias=get_settings().cookie_name)] = None,
) -> MessageOut:
    if token:
        try:
            _, session_id = decode_access_token(token)
            auth_session = await db.get(AuthSession, session_id)
            if auth_session and auth_session.user_id == user.id:
                auth_session.revoked_at = datetime.now(UTC)
                await db.commit()
        except Exception:
            await db.rollback()
    response.delete_cookie(get_settings().cookie_name, path="/")
    return MessageOut(message="Đã đăng xuất")


@router.get("/me", response_model=AuthOut)
async def me(user: CurrentUser, db: DbSession) -> AuthOut:
    return await auth_out(db, user)


@router.post("/telegram-link", response_model=TelegramLinkOut)
async def create_telegram_link(user: CurrentUser, db: DbSession) -> TelegramLinkOut:
    settings = get_settings()
    if not settings.telegram_bot_username:
        raise AppError(503, "TELEGRAM_NOT_CONFIGURED", "Telegram Bot chưa được cấu hình")
    raw_code = generate_invitation_token()
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    db.add(
        TelegramLinkCode(
            user_id=user.id,
            code_hash=hash_invitation_token(raw_code),
            expires_at=expires_at,
        )
    )
    await db.commit()
    return TelegramLinkOut(
        deep_link=f"https://t.me/{settings.telegram_bot_username}?start={raw_code}",
        expires_at=expires_at,
    )
