from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class SkeletonFrameRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    frame_index: int = Field(
        ge=0,
        validation_alias=AliasChoices("frame_index", "frame"),
    )
    timestamp_ms: int | None = Field(default=None, ge=0)
    landmarks: list[list[float]]
    valid: list[bool] | None = None

    @model_validator(mode="after")
    def validate_pose(self) -> SkeletonFrameRequest:
        if len(self.landmarks) != 33:
            raise ValueError("Each frame must contain exactly 33 MediaPipe landmarks")
        normalized: list[list[float]] = []
        for index, point in enumerate(self.landmarks):
            if len(point) not in {3, 4}:
                raise ValueError(f"landmark {index} must contain x, y, z[, visibility]")
            values = [float(value) for value in point]
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"landmark {index} contains a non-finite value")
            if len(values) == 3:
                values.append(1.0)
            if not 0.0 <= values[3] <= 1.0:
                raise ValueError(f"landmark {index} visibility must be in [0, 1]")
            normalized.append(values)
        self.landmarks = normalized
        if self.valid is not None and len(self.valid) != 33:
            raise ValueError("valid must contain exactly 33 boolean values")
        return self


class SkeletonPredictRequest(BaseModel):
    camera_id: str = Field(default="API-CAMERA", min_length=1, max_length=128)
    person_id: str | None = Field(default=None, max_length=128)
    timestamp: datetime | None = None
    fps: float = Field(default=20.0, gt=0, le=240)
    skeleton_sequence: list[SkeletonFrameRequest] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_order(self) -> SkeletonPredictRequest:
        frame_indices = [frame.frame_index for frame in self.skeleton_sequence]
        if any(
            current <= previous
            for previous, current in zip(frame_indices, frame_indices[1:], strict=False)
        ):
            raise ValueError("frame indices must be strictly increasing")
        timestamps = [
            frame.timestamp_ms
            for frame in self.skeleton_sequence
            if frame.timestamp_ms is not None
        ]
        if len(timestamps) > 1 and any(
            current < previous
            for previous, current in zip(timestamps, timestamps[1:], strict=False)
        ):
            raise ValueError("frame timestamps must be monotonically non-decreasing")
        return self


class FeedbackRequest(BaseModel):
    label: Literal["true_fall", "false_alarm", "uncertain"]
    notes: str | None = Field(default=None, max_length=2_000)
    reviewer: str | None = Field(default=None, max_length=128)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    model_ready: bool
    fallback_model: bool
    database: str


class SkeletonPredictResponse(BaseModel):
    camera_id: str
    person_id: str | None
    activity: str
    activity_confidence: float
    posture_probabilities: dict[str, float]
    fall_probability: float
    smoothed_fall_probability: float
    system_state: str
    risk_level: str
    inactive_seconds: float
    self_recovery: bool
    event_id: str | None
    model_version: str
    latency_ms: float
    evidence: list[str]
    timestamp_ms: int


class EventListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
