import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def add_audit(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            group_id=group_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details or {},
        )
    )

