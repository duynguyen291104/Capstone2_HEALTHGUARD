import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import CareGroupMember, ElderProfile, FallAlert, GroupRole, User
from app.services.telegram import telegram_client


async def record_fall(db, elder: ElderProfile, event_id: str, source: str, notify: bool) -> FallAlert:
    """Reserve the event ID before sending; duplicate events never resend."""
    elder_id, group_id, full_name = elder.id, elder.group_id, elder.full_name
    alert = FallAlert(id=event_id, elder_id=elder_id, source=source, telegram_status="pending" if notify else "suppressed", sent=0, failed=0)
    db.add(alert)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.get(FallAlert, event_id)
        if existing is None or existing.elder_id != elder_id:
            raise
        return existing
    if not notify:
        return alert
    chats = list((await db.scalars(
        select(User.telegram_chat_id)
        .join(CareGroupMember, CareGroupMember.user_id == User.id)
        .join(ElderProfile, ElderProfile.group_id == CareGroupMember.group_id)
        .where(
            ElderProfile.id == elder_id, ElderProfile.is_active.is_(True),
            CareGroupMember.group_id == group_id, User.is_active.is_(True), User.telegram_chat_id.is_not(None),
            CareGroupMember.role == GroupRole.OWNER,
        ).distinct()
    )).all())
    # Release the read transaction before performing external I/O.
    await db.commit()
    if not telegram_client.settings.telegram_bot_token:
        alert.telegram_status = "disabled"
    elif not chats:
        alert.telegram_status = "no_recipients"
    else:
        source_label = "camera trực tiếp" if source == "camera" else "video tải lên"
        text = (
            f"[HealthGuard • TÉ NGÃ] {full_name}\n"
            f"FallGuard phát hiện sự kiện trong {source_label}. "
            "Hãy kiểm tra trực tiếp; đây không phải kết luận y tế."
        )
        outcomes = await asyncio.gather(
            *(telegram_client.send_message(chat_id=chat, text=text) for chat in chats), return_exceptions=True,
        )
        alert.sent = sum(bool(getattr(result, "delivered", False)) for result in outcomes)
        alert.failed = len(outcomes) - alert.sent
        alert.telegram_status = "sent" if not alert.failed else "partial" if alert.sent else "failed"
    await db.commit()
    return alert


async def active_elder(db, elder_id: uuid.UUID) -> ElderProfile | None:
    return await db.scalar(select(ElderProfile).where(ElderProfile.id == elder_id, ElderProfile.is_active.is_(True)))
