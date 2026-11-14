import hashlib
import hmac
import time
import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.config import get_settings
from app.dependencies import CurrentUser, DbSession, ElderAccessDep
from app.errors import AppError, not_found
from app.models import CareGroupMember, CaregiverAssignment, ElderProfile, FallAlert, GroupRole, User
from app.services.fall_alerts import active_elder, record_fall

router = APIRouter(tags=["fallguard"])
MAX_BYTES = 50 * 1024 * 1024
NO_STORE = {"Cache-Control": "private, no-store"}


def service_key() -> str:
    key = get_settings().fallguard_service_key
    if not key:
        raise AppError(503, "AI_NOT_CONFIGURED", "AI chưa bật. Hãy chạy HealthGuard với --with-ai.")
    return key


def check_active(access: ElderAccessDep) -> None:
    if not access.elder.is_active:
        raise not_found("Hồ sơ đang chăm sóc")


def client(timeout: float = 5) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{get_settings().fallguard_service_port}", timeout=httpx.Timeout(timeout, connect=3),
        headers={"X-FallGuard-Key": service_key()}, trust_env=False,
    )


def check_upstream(response: httpx.Response) -> None:
    if response.is_success:
        return
    if response.status_code in (404, 409, 413, 415, 422):
        message = {
            404: "Phiên camera hoặc video đã hết hạn.",
            409: "AI đang bận. Hãy tắt camera hoặc chờ video đang xử lý xong.",
            413: "Video tối đa 50 MB.",
            415: "Định dạng video không được hỗ trợ.",
            422: "Không đọc được video hoặc clip dài quá 120 giây.",
        }[response.status_code]
        raise AppError(response.status_code, "AI_REQUEST_FAILED", message)
    raise AppError(503, "AI_UNAVAILABLE", "AI chưa sẵn sàng hoặc không xử lý được dữ liệu.")


async def call_ai(method: str, path: str, **kwargs):
    try:
        async with client(180 if path == "/video" else 5) as session:
            response = await session.request(method, path, **kwargs)
            check_upstream(response)
            return response.json()
    except (httpx.HTTPError, ValueError):
        raise AppError(503, "AI_UNAVAILABLE", "Không kết nối được AI hoặc phân tích quá thời gian. Xem cửa sổ HealthGuard.") from None


async def may_monitor(db, elder: ElderProfile, user_id: uuid.UUID) -> bool:
    member = await db.scalar(
        select(CareGroupMember).join(User, User.id == CareGroupMember.user_id)
        .where(CareGroupMember.group_id == elder.group_id, CareGroupMember.user_id == user_id, User.is_active.is_(True))
    )
    return bool(member and (member.role == GroupRole.OWNER or await db.scalar(
        select(CaregiverAssignment.id).where(
            CaregiverAssignment.elder_id == elder.id, CaregiverAssignment.caregiver_user_id == user_id,
        )
    )))


class CameraOptions(BaseModel):
    index: int = Field(default=0, ge=0, le=9)
    notify: bool = False
    device_code: str = Field(min_length=8, max_length=64)


class CameraEvent(BaseModel):
    elder_id: uuid.UUID
    user_id: uuid.UUID
    event_id: str = Field(pattern=r"^FALL-[0-9]{13,20}-[A-F0-9]{8}$")
    notify: bool = False


@router.post("/fallguard/internal/events")
async def receive_event(payload: CameraEvent, db: DbSession, x_fallguard_key: str = Header(default="")):
    if not hmac.compare_digest(x_fallguard_key.encode(), service_key().encode()):
        raise AppError(403, "AI_KEY_INVALID", "Chỉ dịch vụ AI nội bộ được gửi sự kiện")
    elder = await active_elder(db, payload.elder_id)
    if elder is None or not await may_monitor(db, elder, payload.user_id):
        raise not_found("Hồ sơ được phân công")
    alert = await record_fall(db, elder, payload.event_id, "camera", payload.notify)
    return {"telegram_status": alert.telegram_status}


@router.get("/elders/{elder_id}/fallguard/health")
async def health(access: ElderAccessDep, db: DbSession):
    check_active(access)
    await db.commit()
    return await call_ai("GET", "/health")


@router.get("/elders/{elder_id}/fallguard/events")
async def history(access: ElderAccessDep, db: DbSession, response: Response):
    rows = (await db.scalars(select(FallAlert).where(FallAlert.elder_id == access.elder.id)
            .order_by(FallAlert.created_at.desc(), FallAlert.id).limit(30))).all()
    response.headers.update(NO_STORE)
    return [{"id": row.id, "source": row.source, "telegram_status": row.telegram_status,
             "sent": row.sent, "failed": row.failed,
             "created_at": row.created_at.isoformat() + ("Z" if row.created_at.tzinfo is None else "")} for row in rows]


@router.post("/elders/{elder_id}/fallguard/camera")
async def start_camera(payload: CameraOptions, access: ElderAccessDep, user: CurrentUser, db: DbSession):
    check_active(access)
    service_key()
    device_code = get_settings().fallguard_camera_code
    if not device_code or not hmac.compare_digest(payload.device_code.strip().encode(), device_code.encode()):
        raise AppError(403, "CAMERA_CODE_INVALID", "Nhập đúng mã camera hiện trên PowerShell của máy đang chạy HealthGuard.")
    body = {"index": payload.index, "notify": payload.notify, "elder_id": str(access.elder.id), "user_id": str(user.id)}
    await db.commit()
    return await call_ai("POST", "/camera/start", json=body)


@router.post("/elders/{elder_id}/fallguard/camera/{session_id}/{action}")
async def camera_status(session_id: uuid.UUID, action: Literal["status", "stop"], access: ElderAccessDep, user: CurrentUser, db: DbSession, response: Response):
    if action == "status":
        check_active(access)
    body = {"elder_id": str(access.elder.id), "user_id": str(user.id), "session_id": str(session_id)}
    await db.commit()
    response.headers.update(NO_STORE)
    return await call_ai("POST", f"/camera/{action}", json=body)


def result_signature(elder_id, result_id, user_id, expires):
    return hmac.new(service_key().encode(), f"{elder_id}:{result_id}:{user_id}:{expires}".encode(), hashlib.sha256).hexdigest()


@router.post("/elders/{elder_id}/fallguard/video")
async def analyze_video(request: Request, access: ElderAccessDep, user: CurrentUser, db: DbSession,
                        suffix: Literal[".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"],
                        profile: Literal["normal", "urfd"] = "normal", notify: bool = False):
    check_active(access)
    elder_id, user_id = access.elder.id, user.id
    length = request.headers.get("Content-Length", "")
    if len(length) > 12 or not length.isascii() or not length.isdecimal():
        raise AppError(411, "VIDEO_LENGTH_REQUIRED", "Không xác định được kích thước video.")
    if not 0 < int(length) <= MAX_BYTES:
        raise AppError(413, "VIDEO_TOO_LARGE", "Video phải có dữ liệu và tối đa 50 MB.")
    await db.commit()
    result = await call_ai(
        "POST", "/video", content=request.stream(),
        params={"suffix": suffix, "profile": profile},
    )
    db.expire_all()
    elder = await active_elder(db, elder_id)
    if elder is None or not await may_monitor(db, elder, user_id):
        raise not_found("Hồ sơ được phân công")
    for event_id in result["event_ids"]:
        await record_fall(db, elder, event_id, "video", notify and profile != "urfd")
    result_id = result.pop("result_id")
    result["video_path"] = None
    if result_id:
        expires = int(time.time()) + 3600
        signature = result_signature(elder_id, result_id, user_id, expires)
        result["video_path"] = f"/elders/{elder_id}/fallguard/video/{result_id}?expires={expires}&signature={signature}"
    return result


@router.get("/elders/{elder_id}/fallguard/video/{result_id}")
async def result_video(result_id: uuid.UUID, access: ElderAccessDep, user: CurrentUser, db: DbSession, request: Request,
                       expires: int, signature: str = Query(max_length=64)):
    check_active(access)
    expected = result_signature(access.elder.id, result_id, user.id, expires)
    if expires < time.time() or not hmac.compare_digest(signature.encode(), expected.encode()):
        raise not_found("Video kết quả")
    await db.commit()
    session = client()
    try:
        headers = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
        upstream = await session.send(session.build_request("GET", f"/video/{result_id}", headers=headers), stream=True)
        if upstream.status_code != 416:
            check_upstream(upstream)
    except (httpx.HTTPError, AppError) as error:
        await session.aclose()
        if isinstance(error, AppError):
            raise
        raise AppError(503, "AI_UNAVAILABLE", "Không tải được video kết quả.") from None

    async def close():
        await upstream.aclose()
        await session.aclose()

    response_headers = {key: value for key, value in upstream.headers.items()
                        if key in {"content-length", "content-range", "accept-ranges"}}
    media_type = upstream.headers.get("content-type")
    if media_type not in {"video/mp4", "video/webm"}:
        media_type = "application/octet-stream"
    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await close()

    return StreamingResponse(stream(), status_code=upstream.status_code, media_type=media_type,
                             headers={**response_headers, **NO_STORE}, background=BackgroundTask(close))
