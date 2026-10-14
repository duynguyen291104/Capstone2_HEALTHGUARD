from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from fallguard.domain.enums import PostureLabel, RiskLevel, SystemState


@dataclass(slots=True, frozen=True)
class SequenceRecord:
    dataset: str
    sequence_id: str
    group_id: str
    camera_id: str
    path: Path
    num_frames: int
    fps: float
    sequence_label: str
    annotation_path: Path | None = None


@dataclass(slots=True)
class PoseFrame:
    frame_index: int
    timestamp_ms: int
    landmarks: np.ndarray
    valid: np.ndarray
    bbox_xyxy: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        self.landmarks = np.asarray(self.landmarks, dtype=np.float32)
        self.valid = np.asarray(self.valid, dtype=bool)
        if self.landmarks.shape != (33, 4):
            raise ValueError(f"landmarks must have shape (33, 4), got {self.landmarks.shape}")
        if self.valid.shape != (33,):
            raise ValueError(f"valid must have shape (33,), got {self.valid.shape}")


@dataclass(slots=True, frozen=True)
class Prediction:
    posture: PostureLabel
    posture_probabilities: tuple[float, float, float]
    fall_probability: float
    timestamp_ms: int
    model_version: str = "untrained"
    latency_ms: float = 0.0
    evidence: tuple[str, ...] = ()

    @property
    def confidence(self) -> float:
        return float(max(self.posture_probabilities))


@dataclass(slots=True)
class EventUpdate:
    state: SystemState
    risk_level: RiskLevel
    timestamp_ms: int
    event_id: str | None = None
    inactive_seconds: float = 0.0
    self_recovery: bool = False
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
