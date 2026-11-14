"""Private inference adapter. The standalone FallGuard CLI remains unchanged."""
from __future__ import annotations

import base64
import hmac
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import cv2
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from fallguard.cli import _runtime_components
from fallguard.config import load_config
from fallguard.domain import SystemState
from fallguard.inference.pipeline import InferencePipeline
from fallguard.inference.state_machine import FallStateMachine

ROOT = Path(__file__).resolve().parent
CHECKPOINT = ROOT / "artifacts/urfd_preview_gru/best.pt"
POSE = ROOT / "data/models/pose_landmarker_full.task"
MAX_BYTES = 50 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
LEASE_SECONDS = 25


class CameraStart(BaseModel):
    elder_id: uuid.UUID
    user_id: uuid.UUID
    index: int = Field(ge=0, le=9)
    notify: bool = False


class CameraAccess(BaseModel):
    elder_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID


def build_pipeline(settings):
    if not CHECKPOINT.is_file() or not POSE.is_file():
        raise RuntimeError("Missing trusted FallGuard assets")
    classifier, pose, adapter = _runtime_components(settings, checkpoint=CHECKPOINT, pose_model=POSE)
    return InferencePipeline(
        classifier=classifier, pose_estimator=pose,
        state_machine=FallStateMachine(settings.event_detection),
        window_size=settings.data.window_size, stride=settings.data.stride, window_adapter=adapter,
    )


def browser_preview(source: Path, destination: Path) -> None:
    """Convert the model's mp4v output to VP8 for browsers without mp4v decoding."""
    capture = cv2.VideoCapture(str(source))
    writer = None
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not width or not height:
            return
        scale = min(1, 800 / max(width, height))
        size = (max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2))
        writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"VP80"),
                                 capture.get(cv2.CAP_PROP_FPS) or 20, size)
        if not writer.isOpened():
            return
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(cv2.resize(frame, size))
    finally:
        if writer is not None:
            writer.release()
        capture.release()


def create_app(key: str, backend_port: int = 8000) -> FastAPI:
    if len(key) < 32:
        raise RuntimeError("Run the private AI service via the HealthGuard launcher")
    lock = threading.Lock()
    camera = {}
    video_busy = False

    def forward_fall(spec: CameraStart, event_id: str) -> None:
        for attempt in range(2):
            try:
                response = httpx.post(
                    f"http://127.0.0.1:{backend_port}/api/v1/fallguard/internal/events",
                    headers={"X-FallGuard-Key": key},
                    json={"elder_id": str(spec.elder_id), "user_id": str(spec.user_id), "event_id": event_id, "notify": spec.notify},
                    timeout=20, trust_env=False,
                )
                if response.is_success:
                    return
                if response.status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            if attempt == 0:
                time.sleep(1)
        print("FallGuard: không chuyển được sự kiện camera sang HealthGuard.", flush=True)

    def run_camera(spec: CameraStart, session_id: uuid.UUID):
        capture = pipeline = None
        failure = None
        handled = set()
        try:
            settings = load_config(ROOT / "configs/mvp.yaml")
            pipeline = build_pipeline(settings)
            with lock:
                if camera.get("stop") or time.monotonic() > camera["deadline"]:
                    return
            capture = cv2.VideoCapture(spec.index)
            if not capture.isOpened():
                failure = "Không mở được camera. Kiểm tra số camera và đóng ứng dụng khác đang dùng camera."
                return
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            period = 1 / settings.data.target_fps
            started = time.monotonic()
            next_frame = started
            next_preview = started
            frame_index = 0
            while True:
                with lock:
                    if camera.get("stop") or time.monotonic() > camera["deadline"]:
                        break
                now = time.monotonic()
                if now < next_frame:
                    time.sleep(min(next_frame - now, .05))
                    continue
                ok, frame = capture.read()
                if not ok:
                    failure = "Camera ngừng trả hình."
                    break
                now = time.monotonic()
                next_frame = max(next_frame + period, now)
                result = pipeline.process_frame(frame, frame_index, int((now - started) * 1000))
                frame_index += 1
                event = result.event
                if event and event.event_id and event.metadata.get("just_confirmed") and event.event_id not in handled:
                    handled.add(event.event_id)
                    executor.submit(forward_fall, spec, event.event_id)
                if now < next_preview:
                    continue
                next_preview = now + .12
                canvas = pipeline.annotate_frame(frame, result)
                height, width = canvas.shape[:2]
                if width > 800:
                    canvas = cv2.resize(canvas, (800, max(1, int(height * 800 / width))))
                ok, jpeg = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 73])
                if ok:
                    state = pipeline.latest_event
                    with lock:
                        if not camera.get("stop"):
                            camera.update(
                                status="running", jpeg=base64.b64encode(jpeg).decode("ascii"),
                                state=state.state.value if state else "WARMING_UP",
                            )
        except Exception:
            failure = "Không khởi tạo hoặc xử lý được camera AI. Kiểm tra model và môi trường AI."
        finally:
            if capture is not None:
                capture.release()
            try:
                if pipeline is not None:
                    pipeline.pose_estimator.close()
            finally:
                with lock:
                    if camera.get("session_id") == session_id:
                        camera.update(status="failed" if failure else "stopped", error=failure, jpeg=None, running=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with tempfile.TemporaryDirectory(prefix="healthguard-fallguard-") as folder:
            app.state.folder = Path(folder)
            yield
            with lock:
                camera["stop"] = True

    api = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None, redoc_url=None)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="healthguard-alert")

    @api.middleware("http")
    async def private_only(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get("X-FallGuard-Key", "").encode(), key.encode()):
            return JSONResponse(status_code=403, content={"detail": "Private AI service"})
        return await call_next(request)

    @api.get("/health")
    def health():
        return {"ready": CHECKPOINT.is_file() and POSE.is_file()}

    @api.post("/camera/start")
    def start_camera(spec: CameraStart):
        with lock:
            if camera.get("running") or video_busy:
                raise HTTPException(409, "AI đang sử dụng camera hoặc phân tích video.")
            session_id = uuid.uuid4()
            camera.clear()
            camera.update(
                session_id=session_id, spec=spec, running=True, status="starting",
                deadline=time.monotonic() + LEASE_SECONDS, jpeg=None, error=None, state="WARMING_UP",
            )
            threading.Thread(target=run_camera, args=(spec, session_id), daemon=True, name="healthguard-camera").start()
        return {"session_id": session_id}

    @api.post("/camera/status")
    @api.post("/camera/stop")
    def camera_access(access: CameraAccess, request: Request):
        with lock:
            spec = camera.get("spec")
            if not spec or camera["session_id"] != access.session_id or spec.user_id != access.user_id or spec.elder_id != access.elder_id:
                raise HTTPException(404, "Phiên camera không còn hoạt động.")
            if request.url.path.endswith("/stop"):
                camera.update(stop=True, status="stopped", jpeg=None)
            elif camera["running"] and not camera.get("stop"):
                camera["deadline"] = time.monotonic() + LEASE_SECONDS
            return {key: camera.get(key) for key in ("status", "jpeg", "error", "state")}

    @api.post("/video")
    async def analyze_video(request: Request, suffix: str, profile: Literal["normal", "urfd"] = "normal"):
        nonlocal video_busy
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(415, "Định dạng video không được hỗ trợ.")
        with lock:
            if video_busy or camera.get("running"):
                raise HTTPException(409, "Tắt camera hoặc chờ video trước xử lý xong.")
            video_busy = True
        folder = request.app.state.folder
        result_id = uuid.uuid4()
        output = folder / f"{result_id}.mp4"
        preview = folder / f"{result_id}.webm"
        source = folder / f"{result_id}.upload{suffix}"
        completed = False

        def infer():
            settings = load_config(ROOT / "configs" / ("urfd_demo.yaml" if profile == "urfd" else "mvp.yaml"))
            pipeline = build_pipeline(settings)
            try:
                result = pipeline.run_video(
                    source, output_video=output, target_fps=settings.data.target_fps,
                    crop_right_half=profile == "urfd", max_duration_seconds=120,
                )
                if output.is_file():
                    try:
                        browser_preview(output, preview)
                    except (cv2.error, ValueError, OSError):
                        preview.unlink(missing_ok=True)
                return result
            finally:
                pipeline.pose_estimator.close()

        try:
            # Result files are temporary and expire after an hour.
            for old in folder.iterdir():
                if old.stat().st_mtime < time.time() - 3600:
                    old.unlink(missing_ok=True)
            if sum(f.stat().st_size for f in folder.iterdir()) > 512 * 1024 * 1024:
                raise HTTPException(503, "Bộ nhớ video tạm đã đầy. Khởi động lại HealthGuard.")
            size = 0
            with source.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "Video tối đa 50 MB.")
                    handle.write(chunk)
            if not size:
                raise HTTPException(422, "Video trống.")
            result = await run_in_threadpool(infer)
            confirmed = {
                event["event_id"] for point in result.timeline
                if (event := point.get("event")) and event.get("event_id")
                and event.get("state") == SystemState.CONFIRMED_FALL.value
            }
            confirmed.update(event["event_id"] for event in result.events if event.get("event_id") and event.get("state") == SystemState.CONFIRMED_FALL.value)
            completed = True
            return {
                "event_ids": sorted(confirmed), "frames": result.frame_count,
                "pose_frames": result.pose_frame_count, "predictions": result.prediction_count,
                "result_id": str(result_id) if output.is_file() else None,
                "bounded": result.bounded,
            }
        except ValueError:
            raise HTTPException(422, "Không đọc được video hoặc clip dài quá 120 giây.") from None
        except (RuntimeError, OSError, cv2.error):
            raise HTTPException(503, "AI không phân tích được video. Kiểm tra model và môi trường AI.") from None
        finally:
            try:
                source.unlink(missing_ok=True)
                if not completed:
                    output.unlink(missing_ok=True)
                    preview.unlink(missing_ok=True)
            finally:
                with lock:
                    video_busy = False

    @api.get("/video/{result_id}")
    def video(result_id: uuid.UUID, request: Request):
        file = request.app.state.folder / f"{result_id}.webm"
        mime = "video/webm"
        if not file.is_file() or file.stat().st_size < 1024:
            file = request.app.state.folder / f"{result_id}.mp4"
            mime = "video/mp4"
        if not file.is_file() or file.stat().st_mtime < time.time() - 3600:
            raise HTTPException(404, "Video kết quả đã hết hạn.")
        return FileResponse(file, media_type=mime)

    return api


if __name__ == "__main__":
    uvicorn.run(create_app(os.environ.get("FALLGUARD_SERVICE_KEY", ""), int(os.getenv("FALLGUARD_BACKEND_PORT", "8000"))),
                host="127.0.0.1", port=int(os.getenv("FALLGUARD_SERVICE_PORT", "8001")), access_log=False)
