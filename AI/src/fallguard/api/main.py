from __future__ import annotations

import inspect
import logging
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import numpy as np
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from fallguard import __version__
from fallguard.api.schemas import (
    EventListResponse,
    FeedbackRequest,
    HealthResponse,
    SkeletonPredictRequest,
    SkeletonPredictResponse,
)
from fallguard.api.store import SQLiteEventStore
from fallguard.api.webhook import WebhookNotifier
from fallguard.config import AppConfig
from fallguard.domain import PoseFrame, SystemState
from fallguard.inference import DummyClassifier, FallStateMachine, InferencePipeline
from fallguard.inference.classifier import TemporalClassifier
from fallguard.inference.evidence import extract_video_snapshot
from fallguard.inference.pipeline import (
    VideoInferenceResult,
    event_to_dict,
    pose_window_to_features,
    prediction_to_dict,
)
from fallguard.inference.pose import MediaPipePoseEstimator, NullPoseEstimator, PoseEstimator
from fallguard.notifications import TelegramNotifier

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
LOGGER = logging.getLogger(__name__)


class _DefaultVideoProcessor:
    def __init__(
        self,
        config: AppConfig,
        classifier: TemporalClassifier,
        pose_estimator: PoseEstimator,
        window_adapter: Any | None,
    ) -> None:
        self.config = config
        self.classifier = classifier
        self.pose_estimator = pose_estimator
        self.window_adapter = window_adapter
        self._lock = threading.Lock()

    def run_video(self, input_path: Path, **kwargs: Any) -> VideoInferenceResult:
        pipeline = InferencePipeline(
            classifier=self.classifier,
            pose_estimator=self.pose_estimator,
            state_machine=FallStateMachine(self.config.event_detection),
            window_size=self.config.data.window_size,
            stride=self.config.data.stride,
            window_adapter=self.window_adapter,
        )
        with self._lock:
            return pipeline.run_video(input_path, **kwargs)


def create_app(
    config: AppConfig | None = None,
    *,
    classifier: TemporalClassifier | None = None,
    pose_estimator: PoseEstimator | None = None,
    store: SQLiteEventStore | None = None,
    video_processor: Any | None = None,
    webhook: WebhookNotifier | None = None,
    telegram: TelegramNotifier | None = None,
    runtime_dir: str | Path | None = None,
) -> FastAPI:
    settings = config or AppConfig()
    runtime = Path(runtime_dir) if runtime_dir is not None else Path(settings.paths.runtime_dir)
    result_dir = runtime / "results"
    upload_dir = runtime / "uploads"
    result_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)

    selected_classifier = classifier or _default_classifier(settings)
    selected_pose = pose_estimator or _default_pose_estimator(settings)
    feature_adapter = _model_feature_adapter(settings, selected_classifier)
    selected_store = store or SQLiteEventStore(runtime / "fallguard.db")
    selected_webhook = webhook or WebhookNotifier(
        os.getenv("FALLGUARD_WEBHOOK_URL") or os.getenv("FALLGUARD_ALERT_WEBHOOK"),
        secret=os.getenv("FALLGUARD_WEBHOOK_SECRET"),
    )
    selected_telegram = telegram if telegram is not None else TelegramNotifier.from_env()
    selected_video_processor = video_processor or _DefaultVideoProcessor(
        settings,
        selected_classifier,
        selected_pose,
        feature_adapter,
    )

    application = FastAPI(
        title=settings.project.name,
        version=__version__,
        description="Pose-sequence fall detection and post-fall verification API.",
    )
    application.state.config = settings
    application.state.classifier = selected_classifier
    application.state.pose_estimator = selected_pose
    application.state.event_store = selected_store
    application.state.webhook = selected_webhook
    application.state.telegram = selected_telegram
    application.state.video_processor = selected_video_processor
    application.state.runtime_dir = runtime

    if settings.api.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.api.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    web_dir = Path(__file__).resolve().parents[1] / "web"
    asset_dir = web_dir / "assets"
    application.mount("/static", StaticFiles(directory=asset_dir), name="static")
    application.mount("/artifacts", StaticFiles(directory=result_dir), name="artifacts")

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(web_dir / "index.html", media_type="text/html")

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> dict[str, Any]:
        database_ok = selected_store.ping()
        return {
            "status": "ok" if database_ok else "degraded",
            "service": settings.project.name,
            "version": __version__,
            "model_ready": True,
            "fallback_model": bool(getattr(selected_classifier, "is_fallback", False)),
            "database": "ok" if database_ok else "unavailable",
        }

    @application.get("/v1/model", tags=["system"])
    def model_info() -> dict[str, Any]:
        if hasattr(selected_classifier, "metadata"):
            metadata = dict(selected_classifier.metadata())
        else:
            metadata = {
                "name": type(selected_classifier).__name__,
                "version": getattr(selected_classifier, "model_version", "unknown"),
                "backend": "custom",
                "ready": True,
                "fallback": bool(getattr(selected_classifier, "is_fallback", False)),
            }
            if hasattr(selected_classifier, "input_size"):
                metadata["input_size"] = int(selected_classifier.input_size)
            if hasattr(selected_classifier, "device"):
                metadata["device"] = str(selected_classifier.device)
        metadata.update(
            {
                "window_size": settings.data.window_size,
                "stride": settings.data.stride,
                "target_fps": settings.data.target_fps,
                "causal": True,
                "outputs": ["posture", "fall_probability"],
            }
        )
        return metadata

    @application.post(
        "/v1/predict/skeleton",
        response_model=SkeletonPredictResponse,
        tags=["inference"],
    )
    def predict_skeleton(
        request: SkeletonPredictRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        pipeline = InferencePipeline(
            classifier=selected_classifier,
            pose_estimator=selected_pose,
            state_machine=FallStateMachine(settings.event_detection),
            window_size=settings.data.window_size,
            stride=settings.data.stride,
            window_adapter=feature_adapter,
        )
        final_result = None
        notified_event_ids: set[str] = set()

        def persist_result(result: Any) -> None:
            if result is None or result.prediction is None or result.event is None:
                return
            prediction = result.prediction
            event = result.event
            if event.event_id is None:
                return
            record = event_to_dict(event)
            record.update(
                {
                    "camera_id": request.camera_id,
                    "person_id": request.person_id,
                    "source": "skeleton-api",
                    "fall_probability": prediction.fall_probability,
                }
            )
            stored = selected_store.upsert_event(record)
            if event.metadata.get("just_confirmed") and (event.event_id not in notified_event_ids):
                notified_event_ids.add(event.event_id)
                if selected_webhook.enabled:
                    background_tasks.add_task(selected_webhook.notify, stored)
                if selected_telegram.enabled:
                    background_tasks.add_task(
                        selected_telegram.notify,
                        stored,
                        snapshot_jpeg=None,
                    )

        for position, frame in enumerate(request.skeleton_sequence):
            timestamp_ms = (
                frame.timestamp_ms
                if frame.timestamp_ms is not None
                else int(round(position * 1000.0 / request.fps))
            )
            landmarks = np.asarray(frame.landmarks, dtype=np.float32)
            if frame.valid is None:
                valid = np.isfinite(landmarks).all(axis=1) & (
                    landmarks[:, 3] >= settings.data.min_pose_visibility
                )
            else:
                valid = np.asarray(frame.valid, dtype=bool)
            pose = PoseFrame(
                frame_index=frame.frame_index,
                timestamp_ms=timestamp_ms,
                landmarks=landmarks,
                valid=valid,
                bbox_xyxy=None,
            )
            result = pipeline.process_pose(pose)
            if result.prediction is not None:
                final_result = result
                if result.event is not None and (
                    result.event.metadata.get("state_changed") or result.event.event_id
                ):
                    persist_result(result)

        last_timestamp = (
            request.skeleton_sequence[-1].timestamp_ms
            if request.skeleton_sequence[-1].timestamp_ms is not None
            else int(round((len(request.skeleton_sequence) - 1) * 1000.0 / request.fps))
        )
        if final_result is None or final_result.timestamp_ms != last_timestamp:
            final_result = pipeline.predict_available()
        if final_result is None or final_result.prediction is None or final_result.event is None:
            raise HTTPException(status_code=422, detail="No valid prediction could be produced")

        prediction = final_result.prediction
        event = final_result.event
        persist_result(final_result)

        prediction_data = prediction_to_dict(prediction)
        return {
            "camera_id": request.camera_id,
            "person_id": request.person_id,
            "activity": prediction.posture.name,
            "activity_confidence": prediction.confidence,
            "posture_probabilities": prediction_data["posture_probabilities"],
            "fall_probability": prediction.fall_probability,
            "smoothed_fall_probability": event.metadata["smoothed_fall_probability"],
            "system_state": event.state.value,
            "risk_level": event.risk_level.value,
            "inactive_seconds": event.inactive_seconds,
            "self_recovery": event.self_recovery,
            "event_id": event.event_id,
            "model_version": prediction.model_version,
            "latency_ms": prediction.latency_ms,
            "evidence": list(dict.fromkeys([*prediction.evidence, *event.evidence])),
            "timestamp_ms": prediction.timestamp_ms,
        }

    @application.post("/v1/predict/video", tags=["inference"])
    async def predict_video(
        background_tasks: BackgroundTasks,
        file: Annotated[UploadFile, File()],
        camera_id: str = Query(default="UPLOAD", min_length=1, max_length=128),
        person_id: str | None = Query(default=None, max_length=128),
        urfd_preview: bool = Query(
            default=False,
            description="Crop the RGB right half from an official URFD preview video.",
        ),
    ) -> dict[str, Any]:
        suffix = Path(file.filename or "upload.mp4").suffix.lower()
        if suffix not in ALLOWED_VIDEO_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported video type; allowed: {', '.join(sorted(ALLOWED_VIDEO_SUFFIXES))}",
            )
        request_id = uuid4().hex
        input_path = upload_dir / f"{request_id}{suffix}"
        output_path = result_dir / f"{request_id}-annotated.mp4"
        json_path = result_dir / f"{request_id}-events.json"
        max_upload_mb = _environment_int(
            "FALLGUARD_MAX_UPLOAD_MB",
            default=settings.api.max_upload_mb,
            minimum=1,
        )
        max_bytes = max_upload_mb * 1024 * 1024
        try:
            await _save_upload_limited(file, input_path, max_bytes=max_bytes)
            result = await run_in_threadpool(
                _invoke_video_processor,
                selected_video_processor,
                input_path,
                output_path,
                json_path,
                settings.data.target_fps,
                urfd_preview,
            )
        except _UploadTooLarge as error:
            raise HTTPException(status_code=413, detail=str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        finally:
            await file.close()
            input_path.unlink(missing_ok=True)

        result_data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        stored_events: list[dict[str, Any]] = []
        notified_event_ids: set[str] = set()

        def schedule_alert(
            stored: dict[str, Any],
            event_id: str,
            *,
            timestamp_ms: Any,
        ) -> None:
            if event_id in notified_event_ids:
                return
            notified_event_ids.add(event_id)
            if selected_webhook.enabled:
                background_tasks.add_task(selected_webhook.notify, stored)
            background_tasks.add_task(
                _create_video_evidence_and_notify,
                selected_store,
                selected_telegram,
                stored,
                video_path=output_path,
                snapshot_dir=result_dir / "snapshots",
                event_id=event_id,
                timestamp_ms=timestamp_ms,
            )

        for point in result_data.get("timeline", []):
            event = point.get("event") or {}
            event_id = event.get("event_id")
            metadata = event.get("metadata") or {}
            if not event_id or not metadata.get("just_confirmed"):
                continue
            record = dict(event)
            prediction = point.get("prediction") or {}
            record.update(
                {
                    "camera_id": camera_id,
                    "person_id": person_id,
                    "source": file.filename,
                    "fall_probability": prediction.get(
                        "fall_probability",
                        metadata.get("smoothed_fall_probability", 0.0),
                    ),
                }
            )
            stored = selected_store.upsert_event(record)
            schedule_alert(
                stored,
                str(event_id),
                timestamp_ms=record.get(
                    "timestamp_ms",
                    prediction.get("timestamp_ms", point.get("timestamp_ms", 0)),
                ),
            )
        for event in result_data.get("events", []):
            if not event.get("event_id"):
                continue
            record = dict(event)
            record.update(
                {
                    "camera_id": camera_id,
                    "person_id": person_id,
                    "source": file.filename,
                    "fall_probability": record.get("metadata", {}).get(
                        "smoothed_fall_probability",
                        0.0,
                    ),
                }
            )
            stored = selected_store.upsert_event(record)
            stored_events.append(stored)
            if record.get("state") == SystemState.CONFIRMED_FALL.value:
                schedule_alert(
                    stored,
                    str(record["event_id"]),
                    timestamp_ms=record.get("timestamp_ms", 0),
                )

        result_data["events"] = stored_events
        result_data["camera_id"] = camera_id
        result_data["person_id"] = person_id
        result_data["output_video_url"] = (
            f"/artifacts/{output_path.name}" if output_path.exists() else None
        )
        result_data["events_json_url"] = (
            f"/artifacts/{json_path.name}" if json_path.exists() else None
        )
        result_data.pop("source", None)
        result_data.pop("output_video", None)
        result_data.pop("events_json", None)
        return result_data

    @application.get(
        "/v1/events",
        response_model=EventListResponse,
        tags=["events"],
    )
    def list_events(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        state: str | None = None,
        risk_level: str | None = None,
        camera_id: str | None = None,
    ) -> dict[str, Any]:
        filters = {
            "state": state,
            "risk_level": risk_level,
            "camera_id": camera_id,
        }
        return {
            "items": selected_store.list_events(limit=limit, offset=offset, **filters),
            "total": selected_store.count_events(**filters),
            "limit": limit,
            "offset": offset,
        }

    @application.get("/v1/events/{event_id}", tags=["events"])
    def get_event(event_id: str) -> dict[str, Any]:
        event = selected_store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    @application.post("/v1/events/{event_id}/feedback", tags=["events"])
    def add_feedback(event_id: str, feedback: FeedbackRequest) -> dict[str, Any]:
        event = selected_store.add_feedback(
            event_id,
            label=feedback.label,
            notes=feedback.notes,
            reviewer=feedback.reviewer,
        )
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    @application.get("/v1/metrics/summary", tags=["events"])
    def metrics_summary() -> dict[str, Any]:
        summary = selected_store.summary()
        summary["model_version"] = getattr(selected_classifier, "model_version", "unknown")
        summary["fallback_model"] = bool(getattr(selected_classifier, "is_fallback", False))
        return summary

    return application


class _UploadTooLarge(ValueError):
    pass


async def _save_upload_limited(upload: UploadFile, destination: Path, *, max_bytes: int) -> None:
    received = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                received += len(chunk)
                if received > max_bytes:
                    raise _UploadTooLarge(
                        f"Upload exceeds the configured {max_bytes / 1024 / 1024:g} MB limit"
                    )
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _invoke_video_processor(
    processor: Any,
    input_path: Path,
    output_path: Path,
    json_path: Path,
    target_fps: float,
    crop_right_half: bool = False,
) -> Any:
    callable_processor = getattr(processor, "run_video", processor)
    kwargs = {
        "output_video": output_path,
        "events_json": json_path,
        "target_fps": target_fps,
        "crop_right_half": crop_right_half,
        "max_duration_seconds": 300.0,
    }
    try:
        signature = inspect.signature(callable_processor)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    result = callable_processor(input_path, **kwargs)
    if not isinstance(result, Mapping) and not hasattr(result, "to_dict"):
        raise TypeError("video_processor must return a mapping or VideoInferenceResult")
    return result


def _try_extract_video_snapshot(
    video_path: Path,
    snapshot_dir: Path,
    *,
    event_id: str,
    timestamp_ms: Any,
) -> Path | None:
    safe_event_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", event_id).strip("._")[:120]
    if not safe_event_id:
        safe_event_id = "fall-event"
    destination = snapshot_dir / f"{safe_event_id}.jpg"
    try:
        event_timestamp_ms = max(0, int(float(timestamp_ms)))
        return extract_video_snapshot(
            video_path,
            destination,
            timestamp_ms=event_timestamp_ms,
        )
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError) as error:
        LOGGER.warning(
            "Could not extract Telegram evidence for event %s; sending text only: %s",
            event_id,
            error,
        )
        return None


def _create_video_evidence_and_notify(
    store: SQLiteEventStore,
    telegram: TelegramNotifier,
    event: dict[str, Any],
    *,
    video_path: Path,
    snapshot_dir: Path,
    event_id: str,
    timestamp_ms: Any,
) -> None:
    """Create local evidence and send Telegram without blocking the request."""

    snapshot_path = _try_extract_video_snapshot(
        video_path,
        snapshot_dir,
        event_id=event_id,
        timestamp_ms=timestamp_ms,
    )
    notification_event = dict(event)
    if snapshot_path is not None:
        try:
            persisted_event = store.get_event(event_id) or dict(event)
            metadata = dict(persisted_event.get("metadata") or {})
            metadata["snapshot_path"] = str(snapshot_path)
            persisted_event["metadata"] = metadata
            store.upsert_event(persisted_event)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            LOGGER.warning(
                "Could not persist snapshot metadata for event %s: %s",
                event_id,
                error,
            )

        notification_metadata = dict(notification_event.get("metadata") or {})
        notification_metadata["snapshot_path"] = str(snapshot_path)
        notification_event["metadata"] = notification_metadata
    if telegram.enabled:
        try:
            telegram.notify(notification_event, snapshot_jpeg=snapshot_path)
        except Exception:  # pragma: no cover - defensive integration boundary
            LOGGER.exception("Unexpected Telegram notifier failure for event %s", event_id)


def _model_feature_adapter(
    settings: AppConfig,
    classifier: TemporalClassifier,
) -> Any | None:
    """Bridge checkpoint predictors that consume numeric feature arrays."""

    if not hasattr(classifier, "input_size"):
        return None

    def adapt(window: Any) -> np.ndarray:
        return pose_window_to_features(
            window,
            max_interpolation_gap=settings.data.max_interpolation_gap,
            include_xyz=settings.features.include_xyz,
            include_visibility=settings.features.include_visibility,
            include_velocity=settings.features.include_velocity,
            include_geometry=settings.features.include_geometry,
        )

    return adapt


def _default_classifier(settings: AppConfig) -> TemporalClassifier:
    checkpoint = os.getenv("FALLGUARD_MODEL_PATH")
    if not checkpoint:
        return DummyClassifier()
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.is_file():
        return DummyClassifier(f"checkpoint not found: {checkpoint_path}")
    try:
        from fallguard.models.predictor import TemporalPredictor

        return TemporalPredictor.from_checkpoint(
            checkpoint_path,
            device=settings.project.device,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return DummyClassifier(f"checkpoint could not be loaded: {error}")


def _default_pose_estimator(settings: AppConfig) -> PoseEstimator:
    data_model_dir = Path(settings.paths.raw_dir).parent / "models"
    candidates = [
        os.getenv("FALLGUARD_POSE_MODEL") or os.getenv("FALLGUARD_POSE_MODEL_PATH"),
        str(data_model_dir / "pose_landmarker_full.task"),
        str(data_model_dir / "pose_landmarker_lite.task"),
        str(Path(settings.paths.artifact_dir) / "pose_landmarker_full.task"),
        str(Path(settings.paths.artifact_dir) / "pose_landmarker_lite.task"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                return MediaPipePoseEstimator(
                    candidate,
                    min_visibility=settings.data.min_pose_visibility,
                )
            except (OSError, RuntimeError, ValueError):
                continue
    return NullPoseEstimator()


def _environment_int(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


app = create_app()
