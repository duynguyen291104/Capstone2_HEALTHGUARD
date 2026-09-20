import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.dependencies import DbSession
from app.errors import AppError
from app.models import TelegramLinkCode
from app.schemas import MessageOut
from app.security import hash_invitation_token
from app.services.telegram import telegram_client


router = APIRouter(prefix="/integrations/telegram", tags=["telegram"])


@router.post("/webhook", response_model=MessageOut, include_in_schema=False)
async def telegram_webhook(
    update: dict[str, Any],
    db: DbSession,
    secret_header: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
) -> MessageOut:
    configured_secret = get_settings().telegram_webhook_secret
    if not configured_secret or not secret_header or not secrets.compare_digest(
        configured_secret, secret_header
    ):
        raise AppError(401, "INVALID_TELEGRAM_SECRET", "Webhook không hợp lệ")

    message = update.get("message") or {}
    text = str(message.get("text") or "")
    chat_id = (message.get("chat") or {}).get("id")
    if not text.startswith("/start ") or chat_id is None:
        return MessageOut(message="ignored")
    raw_code = text.split(maxsplit=1)[1].strip()
    link_code = await db.scalar(
        select(TelegramLinkCode)
        .options(selectinload(TelegramLinkCode.user))
        .where(TelegramLinkCode.code_hash == hash_invitation_token(raw_code))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if not link_code:
        await telegram_client.send_message(
            chat_id=str(chat_id), text="Mã liên kết HealthGuard không hợp lệ."
        )
        return MessageOut(message="invalid code")
    expires_at = link_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if link_code.used_at or expires_at <= now:
        await telegram_client.send_message(
            chat_id=str(chat_id), text="Mã liên kết HealthGuard đã hết hạn hoặc đã được dùng."
        )
        return MessageOut(message="expired code")

    link_code.user.telegram_chat_id = str(chat_id)
    link_code.used_at = now
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await telegram_client.send_message(
            chat_id=str(chat_id), text="Tài khoản Telegram này đã được liên kết."
        )
        return MessageOut(message="chat already linked")
    await telegram_client.send_message(
        chat_id=str(chat_id), text="Đã liên kết Telegram với tài khoản HealthGuard."
    )
    return MessageOut(message="linked")
