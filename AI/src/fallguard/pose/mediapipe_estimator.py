from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Protocol

import numpy as np

from fallguard.data.download import DownloadResult, download_file
from fallguard.domain.types import PoseFrame

POSE_MODEL_URLS = {
    "lite": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
    ),
    "full": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
    ),
    "heavy": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
    ),
}


class PoseEstimator(Protocol):
    def process(
        self, frame_bgr: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> PoseFrame | None: ...

    def close(self) -> None: ...


def download_pose_model(
    model_dir: str | Path,
    *,
    variant: Literal["lite", "full", "heavy"] = "lite",
    retries: int = 4,
) -> DownloadResult:
    """Download a published MediaPipe Tasks pose-landmarker model asset."""

    if variant not in POSE_MODEL_URLS:
        raise ValueError(f"Unknown pose model variant: {variant!r}")
    destination = Path(model_dir) / f"pose_landmarker_{variant}.task"
    return download_file(POSE_MODEL_URLS[variant], destination, retries=retries)


class MediaPipePoseEstimator:
    """MediaPipe Tasks ``VIDEO`` pose extractor implementing the runtime contract."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        min_visibility: float = 0.35,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        num_poses: int = 1,
    ) -> None:
        if not 0.0 <= min_visibility <= 1.0:
            raise ValueError("min_visibility must be in [0, 1]")
        if num_poses < 1:
            raise ValueError("num_poses must be positive")
        asset = Path(model_path)
        if not asset.is_file():
            raise FileNotFoundError(f"MediaPipe pose model not found: {asset}")

        try:
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError(
                "MediaPipe is not installed. Install the project dependencies "
                "before extracting real poses."
            ) from exc

        self._mp = mp
        self._min_visibility = float(min_visibility)
        self._last_timestamp_ms: int | None = None
        base_options = mp.tasks.BaseOptions(model_asset_path=str(asset))
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=num_poses,
            min_pose_detection_confidence=min_pose_detection_confidence,
            min_pose_presence_confidence=min_pose_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_segmentation_masks=False,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)

    def process(
        self, frame_bgr: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> PoseFrame | None:
        frame = np.asarray(frame_bgr)
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError(f"frame_bgr must have shape [H, W, 3], got {frame.shape}")
        if frame_index < 0 or timestamp_ms < 0:
            raise ValueError("frame_index and timestamp_ms must be non-negative")
        if (
            self._last_timestamp_ms is not None
            and timestamp_ms <= self._last_timestamp_ms
        ):
            raise ValueError(
                "MediaPipe VIDEO timestamps must be strictly increasing "
                f"({timestamp_ms} <= {self._last_timestamp_ms})"
            )

        rgb = np.ascontiguousarray(frame[..., ::-1])
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=rgb,
        )
        result = self._landmarker.detect_for_video(image, int(timestamp_ms))
        self._last_timestamp_ms = int(timestamp_ms)
        if not result.pose_landmarks:
            return None

        def confidence(pose: list[Any]) -> float:
            values = [float(getattr(point, "visibility", 0.0) or 0.0) for point in pose]
            return float(np.mean(values)) if values else 0.0

        pose = max(result.pose_landmarks, key=confidence)
        if len(pose) != 33:
            raise RuntimeError(f"MediaPipe returned {len(pose)} landmarks instead of 33")
        landmarks = np.asarray(
            [
                (
                    float(point.x),
                    float(point.y),
                    float(point.z),
                    float(getattr(point, "visibility", 0.0) or 0.0),
                )
                for point in pose
            ],
            dtype=np.float32,
        )
        valid = (
            np.isfinite(landmarks).all(axis=1)
            & (landmarks[:, 3] >= self._min_visibility)
        )
        visible_xy = landmarks[valid, :2]
        bbox = None
        if len(visible_xy):
            minimum = visible_xy.min(axis=0)
            maximum = visible_xy.max(axis=0)
            bbox = (
                float(minimum[0]),
                float(minimum[1]),
                float(maximum[0]),
                float(maximum[1]),
            )
        landmarks[~np.isfinite(landmarks)] = 0.0
        return PoseFrame(
            frame_index=int(frame_index),
            timestamp_ms=int(timestamp_ms),
            landmarks=landmarks,
            valid=valid,
            bbox_xyxy=bbox,
        )

    def close(self) -> None:
        landmarker = getattr(self, "_landmarker", None)
        if landmarker is not None:
            landmarker.close()
            self._landmarker = None

    def __enter__(self) -> MediaPipePoseEstimator:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
