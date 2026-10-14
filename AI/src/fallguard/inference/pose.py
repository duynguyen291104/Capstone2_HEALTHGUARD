from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from fallguard.domain import PoseFrame
from fallguard.pose.mediapipe_estimator import (
    MediaPipePoseEstimator as TasksMediaPipePoseEstimator,
)


@runtime_checkable
class PoseEstimator(Protocol):
    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        timestamp_ms: int,
    ) -> PoseFrame | None:
        """Extract one MediaPipe-layout skeleton from a BGR frame."""


class NullPoseEstimator:
    """Safe fallback used when the MediaPipe model asset is unavailable."""

    is_fallback = True

    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        timestamp_ms: int,
    ) -> None:
        del frame_bgr, frame_index, timestamp_ms
        return None

    def close(self) -> None:
        return None


class MediaPipePoseEstimator:
    """Resettable wrapper around the project's MediaPipe Tasks estimator.

    MediaPipe ``VIDEO`` mode requires timestamps to increase for the lifetime
    of a landmarker. Recreating the delegate on ``reset`` lets one API process
    safely analyze multiple independent videos whose timelines each start at
    zero.
    """

    is_fallback = False

    def __init__(self, model_path: str | Path, **options: Any) -> None:
        self.model_path = Path(model_path)
        self.options = dict(options)
        self._delegate: TasksMediaPipePoseEstimator | None = None

    def _estimator(self) -> TasksMediaPipePoseEstimator:
        if self._delegate is None:
            self._delegate = TasksMediaPipePoseEstimator(
                self.model_path,
                **self.options,
            )
        return self._delegate

    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        timestamp_ms: int,
    ) -> PoseFrame | None:
        return self._estimator().process(frame_bgr, frame_index, timestamp_ms)

    def reset(self) -> None:
        self.close()

    def close(self) -> None:
        if self._delegate is not None:
            self._delegate.close()
            self._delegate = None


__all__ = ["MediaPipePoseEstimator", "NullPoseEstimator", "PoseEstimator"]
