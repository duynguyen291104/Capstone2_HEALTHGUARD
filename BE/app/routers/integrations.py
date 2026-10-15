import secrets
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.dependencies import DbSession
from app.errors import AppError
from app.models import TelegramLinkCode, User
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
        configured_secret.encode("utf-8"), secret_header.encode("utf-8")
    ):
        raise AppError(401, "INVALID_TELEGRAM_SECRET", "Webhook không hợp lệ")

    message = update.get("message")
    if not isinstance(message, dict):
        return MessageOut(message="ignored")
    text, chat, sender = message.get("text"), message.get("chat"), message.get("from")
    if not isinstance(text, str) or not isinstance(chat, dict) or not isinstance(sender, dict):
        return MessageOut(message="ignored")
    chat_id = chat.get("id")
    # Health notifications contain private medical details. Linking a family
    # group/supergroup/channel would expose them to everyone in that chat.
    if (
        chat.get("type") != "private"
        or not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id <= 0
        or sender.get("id") != chat_id
        or not text.startswith("/start ")
    ):
        return MessageOut(message="ignored")
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return MessageOut(message="invalid code")
    raw_code = parts[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,200}", raw_code):
        return MessageOut(message="invalid code")
    code_hash = hash_invitation_token(raw_code)
    # Resolve the user without locking, then use User -> Code lock order,
    # exactly as code issuance does, to avoid deadlocks on relinking.
    user_id = await db.scalar(select(TelegramLinkCode.user_id).where(TelegramLinkCode.code_hash == code_hash))
    linked_user = await db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)) if user_id else None
    link_code = await db.scalar(
        select(TelegramLinkCode)
        .where(TelegramLinkCode.code_hash == code_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if not link_code:
        await telegram_client.send_message(
            chat_id=str(chat_id), text="Mã liên kết HealthGuard không hợp lệ."
        )
        return MessageOut(message="invalid code")
    if not linked_user or not linked_user.is_active:
        return MessageOut(message="account unavailable")
    expires_at = link_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if link_code.used_at or expires_at <= now:
        await telegram_client.send_message(
            chat_id=str(chat_id), text="Mã liên kết HealthGuard đã hết hạn hoặc đã được dùng."
        )
        return MessageOut(message="expired code")

    linked_user.telegram_chat_id = str(chat_id)
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
