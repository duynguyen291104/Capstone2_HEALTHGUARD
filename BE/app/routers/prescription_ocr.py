from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.audit import add_audit
from app.config import get_settings
from app.dependencies import CurrentUser, DbSession, ElderAccessDep
from app.errors import AppError
from app.models import AuditLog, User
from app.services.prescription_ocr import MAX_BYTES, OcrResult, extract_prescription, normalize_image

router = APIRouter(tags=["prescription-ocr"])


@router.post("/elders/{elder_id}/prescription-ocr", response_model=OcrResult)
async def read_prescription(request: Request, response: Response, access: ElderAccessDep, user: CurrentUser, db: DbSession) -> OcrResult:
    if not access.is_owner:
        raise AppError(403, "FORBIDDEN", "Chỉ chủ nhóm được nhập đơn thuốc từ ảnh")
    if request.query_params.get("consent") != "true":
        raise AppError(400, "OCR_CONSENT_REQUIRED", "Cần xác nhận quyền sử dụng và đúng người trên đơn thuốc")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise AppError(413, "IMAGE_TOO_LARGE", "Ảnh không được vượt quá 5 MB")
        data.extend(chunk)
    clean = await run_in_threadpool(normalize_image, bytes(data))
    # Lock per account so concurrent requests cannot bypass the rate limit.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    count = await db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.actor_user_id == user.id,
        AuditLog.action == "PRESCRIPTION_OCR_REQUESTED",
        AuditLog.created_at >= datetime.now(UTC) - timedelta(hours=1),
    ))
    if count >= get_settings().ocr_requests_per_hour:
        raise AppError(429, "OCR_RATE_LIMIT", "Bạn đã đạt giới hạn đọc đơn trong một giờ. Hãy nhập thủ công hoặc thử lại sau.")
    add_audit(db, group_id=access.context.group_id, actor_user_id=user.id,
              action="PRESCRIPTION_OCR_REQUESTED", entity_type="elder", entity_id=access.elder.id,
              details={"provider": "tesseract_local"})
    await db.commit()  # Count attempted OCR jobs; never log image or OCR text.
    response.headers["Cache-Control"] = "no-store"
    return await extract_prescription(clean)
