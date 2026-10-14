from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from fallguard.domain.enums import PostureLabel
from fallguard.domain.types import PoseFrame, Prediction


@dataclass(slots=True, frozen=True)
class RuleConfig:
    """Interpretable thresholds for the non-learned comparison baseline."""

    lying_verticality: float = 0.48
    transition_verticality: float = 0.72
    rapid_hip_drop_per_second: float = 0.32
    orientation_change: float = 0.28
    fall_threshold: float = 0.50


class RuleBasedFallDetector:
    """Simple pose-geometry baseline used as an honest model comparison.

    Coordinates are expected to follow MediaPipe convention (normalized image
    ``x``/``y``). The baseline is intentionally small and interpretable; it is
    not presented as a learned model.
    """

    model_version = "pose-rule-v1"

    def __init__(self, config: RuleConfig | None = None) -> None:
        self.config = config or RuleConfig()

    def predict(
        self,
        window: np.ndarray | Sequence[PoseFrame],
        *,
        timestamps_ms: np.ndarray | Sequence[int] | None = None,
        timestamp_ms: int | None = None,
    ) -> Prediction:
        landmarks, inferred_timestamps = _coerce_landmarks(window)
        if timestamps_ms is None:
            timestamps_ms = inferred_timestamps
        times = _coerce_timestamps(timestamps_ms, landmarks.shape[0])
        if timestamp_ms is None:
            timestamp_ms = int(times[-1]) if times is not None else 0

        verticality = _body_verticality(landmarks)
        final_verticality = float(verticality[-1])
        start_count = max(1, min(5, len(verticality) // 3 or 1))
        initial_verticality = float(np.nanmedian(verticality[:start_count]))
        orientation_drop = max(0.0, initial_verticality - final_verticality)
        hip_drop_speed = _maximum_hip_drop_speed(landmarks, times)

        posture_probabilities = _posture_probabilities(
            final_verticality,
            hip_drop_speed,
            self.config,
        )
        posture = PostureLabel(int(np.argmax(posture_probabilities)))

        drop_component = _soft_threshold(
            hip_drop_speed,
            self.config.rapid_hip_drop_per_second,
            scale=10.0,
        )
        orientation_component = _soft_threshold(
            orientation_drop,
            self.config.orientation_change,
            scale=12.0,
        )
        lying_component = _soft_threshold(
            self.config.transition_verticality - final_verticality,
            0.0,
            scale=8.0,
        )
        # A rapid vertical displacement is essential; orientation and final
        # posture prevent deliberate small movements from dominating.
        fall_probability = float(
            np.clip(
                0.50 * drop_component + 0.30 * orientation_component + 0.20 * lying_component,
                0.0,
                1.0,
            )
        )

        evidence: list[str] = []
        if hip_drop_speed >= self.config.rapid_hip_drop_per_second:
            evidence.append("rapid hip height reduction")
        if orientation_drop >= self.config.orientation_change:
            evidence.append("body changed from vertical toward horizontal")
        if final_verticality <= self.config.lying_verticality:
            evidence.append("horizontal body posture")

        return Prediction(
            posture=posture,
            posture_probabilities=tuple(float(value) for value in posture_probabilities),
            fall_probability=fall_probability,
            timestamp_ms=int(timestamp_ms),
            model_version=self.model_version,
            evidence=tuple(evidence),
        )

    def __call__(self, window: np.ndarray | Sequence[PoseFrame], **kwargs: Any) -> Prediction:
        return self.predict(window, **kwargs)


# Common names retained for scripts/notebooks.
RuleBasedClassifier = RuleBasedFallDetector
PoseRuleBaseline = RuleBasedFallDetector


def _coerce_landmarks(
    window: np.ndarray | Sequence[PoseFrame],
) -> tuple[np.ndarray, np.ndarray | None]:
    if isinstance(window, Sequence) and window and isinstance(window[0], PoseFrame):
        frames = list(window)
        landmarks = np.stack([frame.landmarks for frame in frames]).astype(np.float32)
        timestamps = np.asarray([frame.timestamp_ms for frame in frames], dtype=np.int64)
        return landmarks, timestamps

    values = np.asarray(window, dtype=np.float32)
    if values.ndim == 2:
        feature_count = values.shape[1]
        if feature_count in {33 * 3, 33 * 4}:
            values = values.reshape(values.shape[0], 33, feature_count // 33)
        else:
            raise ValueError(
                "A flattened rule-baseline window must contain exactly 99 or 132 "
                "pose-coordinate features per timestep"
            )
    if values.ndim != 3 or values.shape[1] != 33 or values.shape[2] < 2:
        raise ValueError(
            "Rule baseline expects [time, 33, channels] landmarks or a sequence "
            "of PoseFrame objects"
        )
    if values.shape[0] < 1:
        raise ValueError("Rule baseline requires at least one frame")
    return values, None


def _coerce_timestamps(
    timestamps_ms: np.ndarray | Sequence[int] | None,
    length: int,
) -> np.ndarray | None:
    if timestamps_ms is None:
        return None
    times = np.asarray(timestamps_ms, dtype=np.float64)
    if times.shape != (length,):
        raise ValueError(f"timestamps_ms must have shape ({length},)")
    if length > 1 and np.any(np.diff(times) <= 0):
        raise ValueError("timestamps_ms must be strictly increasing")
    return times


def _body_verticality(landmarks: np.ndarray) -> np.ndarray:
    shoulders = np.nanmean(landmarks[:, [11, 12], :2], axis=1)
    hips = np.nanmean(landmarks[:, [23, 24], :2], axis=1)
    body_axis = hips - shoulders
    magnitude = np.linalg.norm(body_axis, axis=1)
    return np.divide(
        np.abs(body_axis[:, 1]),
        magnitude,
        out=np.zeros_like(magnitude),
        where=magnitude > 1e-6,
    )


def _maximum_hip_drop_speed(
    landmarks: np.ndarray,
    timestamps_ms: np.ndarray | None,
) -> float:
    if landmarks.shape[0] < 2:
        return 0.0
    hip_y = np.nanmean(landmarks[:, [23, 24], 1], axis=1)
    if timestamps_ms is None:
        # The project default is 20 FPS. Using it here keeps the rule baseline
        # usable on a plain array while explicit timestamps remain preferable.
        delta_seconds = np.full(landmarks.shape[0] - 1, 1.0 / 20.0)
    else:
        delta_seconds = np.diff(timestamps_ms) / 1000.0
    speeds = np.diff(hip_y) / delta_seconds
    finite = speeds[np.isfinite(speeds)]
    return float(max(0.0, np.max(finite))) if finite.size else 0.0


def _posture_probabilities(
    verticality: float,
    hip_drop_speed: float,
    config: RuleConfig,
) -> np.ndarray:
    upright = _soft_threshold(
        verticality,
        config.transition_verticality,
        scale=10.0,
    )
    lying = _soft_threshold(
        config.lying_verticality - verticality,
        0.0,
        scale=10.0,
    )
    center = (config.lying_verticality + config.transition_verticality) / 2.0
    half_width = max(
        (config.transition_verticality - config.lying_verticality) / 2.0,
        1e-3,
    )
    transition = float(np.exp(-0.5 * ((verticality - center) / half_width) ** 2))
    transition = max(
        transition,
        0.75
        * _soft_threshold(
            hip_drop_speed,
            config.rapid_hip_drop_per_second,
            scale=10.0,
        ),
    )
    scores = np.asarray([upright, transition, lying], dtype=np.float64) + 1e-6
    return scores / scores.sum()


def _soft_threshold(value: float, threshold: float, *, scale: float) -> float:
    argument = float(np.clip(scale * (value - threshold), -60.0, 60.0))
    return float(1.0 / (1.0 + np.exp(-argument)))
