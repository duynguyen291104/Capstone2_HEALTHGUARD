from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import numpy as np

from fallguard.config import EventDetectionConfig
from fallguard.domain import (
    EventUpdate,
    PoseFrame,
    PostureLabel,
    Prediction,
    RiskLevel,
    SystemState,
)


@dataclass(slots=True)
class _PoseObservation:
    timestamp_ms: int
    xyz: np.ndarray
    valid: np.ndarray


class FallStateMachine:
    """Timestamp-driven post-fall verification and event deduplication.

    A high probability opens a candidate. Confirmation additionally requires a
    high-confidence fall signal, sustained lying posture, and measured pose
    inactivity. Recovery is deliberately a transparent upright-pose heuristic.
    """

    def __init__(
        self,
        config: EventDetectionConfig | None = None,
        *,
        motion_threshold: float = 0.012,
        max_pose_gap_seconds: float = 1.0,
    ) -> None:
        if motion_threshold < 0:
            raise ValueError("motion_threshold must be non-negative")
        if max_pose_gap_seconds <= 0:
            raise ValueError("max_pose_gap_seconds must be positive")
        self.config = config or EventDetectionConfig()
        self.motion_threshold = float(motion_threshold)
        self.max_pose_gap_ms = int(max_pose_gap_seconds * 1000)
        self.reset()

    def reset(self) -> None:
        self.state = SystemState.NORMAL
        self.ema_probability: float | None = None
        self.event_id: str | None = None
        self._last_timestamp_ms: int | None = None
        self._candidate_since_ms: int | None = None
        self._possible_since_ms: int | None = None
        self._lying_since_ms: int | None = None
        self._upright_since_ms: int | None = None
        self._inactive_since_ms: int | None = None
        self._confirmed_at_ms: int | None = None
        self._last_pose: _PoseObservation | None = None
        self._last_motion: float | None = None
        self._peak_probability = 0.0
        self._high_confidence_seen = False
        self._evidence: list[str] = []

    @property
    def inactive_seconds(self) -> float:
        if self._inactive_since_ms is None or self._last_timestamp_ms is None:
            return 0.0
        return max(0.0, (self._last_timestamp_ms - self._inactive_since_ms) / 1000.0)

    @property
    def cooldown_until_ms(self) -> int | None:
        if self._confirmed_at_ms is None:
            return None
        return self._confirmed_at_ms + int(self.config.cooldown_seconds * 1000)

    def update(self, prediction: Prediction, pose: PoseFrame | None = None) -> EventUpdate:
        timestamp_ms = int(prediction.timestamp_ms)
        if self._last_timestamp_ms is not None and timestamp_ms < self._last_timestamp_ms:
            raise ValueError("Prediction timestamps must be monotonically non-decreasing")
        if not 0.0 <= prediction.fall_probability <= 1.0:
            raise ValueError("fall_probability must be in [0, 1]")

        previous_state = self.state
        self._last_timestamp_ms = timestamp_ms
        self._update_ema(float(prediction.fall_probability))
        self._observe_pose(pose, candidate_active=self.state is not SystemState.NORMAL)

        if self.state is SystemState.NORMAL:
            self._handle_normal(prediction, pose)
        elif self.state is SystemState.POSSIBLE_FALL:
            self._observe_candidate(prediction, pose)
            self._handle_possible(prediction, pose)
        elif self.state is SystemState.VERIFYING:
            self._observe_candidate(prediction, pose)
            self._handle_verifying(prediction, pose)
        elif self.state is SystemState.CONFIRMED_FALL:
            self._handle_confirmed(prediction, pose)
        elif self.state is SystemState.RECOVERED:
            self._handle_recovered(timestamp_ms)

        return self._make_update(prediction, previous_state)

    def _update_ema(self, probability: float) -> None:
        if self.ema_probability is None:
            self.ema_probability = probability
        else:
            alpha = self.config.ema_alpha
            self.ema_probability = alpha * probability + (1.0 - alpha) * self.ema_probability
        self._peak_probability = max(self._peak_probability, self.ema_probability)
        if self.ema_probability >= self.config.confirm_fall_probability:
            self._high_confidence_seen = True

    def _handle_normal(self, prediction: Prediction, pose: PoseFrame | None) -> None:
        cooldown_until = self.cooldown_until_ms
        if cooldown_until is not None and prediction.timestamp_ms < cooldown_until:
            return
        if self.ema_probability is None:
            return
        if self.ema_probability >= self.config.possible_fall_probability:
            self.state = SystemState.POSSIBLE_FALL
            self._candidate_since_ms = prediction.timestamp_ms
            self._possible_since_ms = prediction.timestamp_ms
            self._lying_since_ms = (
                prediction.timestamp_ms if prediction.posture is PostureLabel.LYING else None
            )
            self._upright_since_ms = None
            self._inactive_since_ms = None
            self._last_pose = _pose_observation(pose)
            self._add_evidence("Fall probability crossed the candidate threshold")
            if self.config.possible_fall_min_seconds == 0:
                self.state = SystemState.VERIFYING

    def _handle_possible(self, prediction: Prediction, pose: PoseFrame | None) -> None:
        assert self._possible_since_ms is not None
        elapsed = (prediction.timestamp_ms - self._possible_since_ms) / 1000.0
        cancel_threshold = self.config.possible_fall_probability * 0.65
        if (
            self.ema_probability is not None
            and self.ema_probability < cancel_threshold
            and prediction.posture is PostureLabel.UPRIGHT
        ):
            self._clear_candidate(keep_cooldown=True)
            self.state = SystemState.NORMAL
            return
        if elapsed >= self.config.possible_fall_min_seconds:
            self.state = SystemState.VERIFYING
            self._add_evidence("Fall signal persisted for the minimum candidate duration")
            self._handle_verifying(prediction, pose)

    def _handle_verifying(self, prediction: Prediction, pose: PoseFrame | None) -> None:
        if self._is_upright(prediction, pose):
            if self._upright_since_ms is None:
                self._upright_since_ms = prediction.timestamp_ms
            upright_seconds = (prediction.timestamp_ms - self._upright_since_ms) / 1000.0
            if upright_seconds >= self.config.recovery_upright_seconds:
                self.state = SystemState.RECOVERED
                self._add_evidence("Sustained upright posture indicates self-recovery")
                return
        else:
            self._upright_since_ms = None

        lying_seconds = self._elapsed_since(self._lying_since_ms, prediction.timestamp_ms)
        inactive_seconds = self.inactive_seconds
        if (
            self._high_confidence_seen
            and lying_seconds >= self.config.fallen_min_seconds
            and inactive_seconds >= self.config.inactive_seconds
        ):
            self.state = SystemState.CONFIRMED_FALL
            self._confirmed_at_ms = prediction.timestamp_ms
            if self.event_id is None:
                self.event_id = _new_event_id(prediction.timestamp_ms)
            self._add_evidence("Lying posture persisted after the fall signal")
            if self.config.inactive_seconds > 0:
                self._add_evidence(
                    "No significant pose movement during post-fall verification"
                )
            else:
                self._add_evidence(
                    "Inactivity duration was not required by the benchmark profile"
                )

    def _handle_confirmed(self, prediction: Prediction, pose: PoseFrame | None) -> None:
        if self._is_upright(prediction, pose):
            if self._upright_since_ms is None:
                self._upright_since_ms = prediction.timestamp_ms
            upright_seconds = (prediction.timestamp_ms - self._upright_since_ms) / 1000.0
            if upright_seconds >= self.config.recovery_upright_seconds:
                self.state = SystemState.RECOVERED
                self._add_evidence("Sustained upright posture indicates self-recovery")
        else:
            self._upright_since_ms = None

    def _handle_recovered(self, timestamp_ms: int) -> None:
        cooldown_until = self.cooldown_until_ms
        if cooldown_until is None or timestamp_ms >= cooldown_until:
            self.state = SystemState.NORMAL
            self._clear_candidate(keep_cooldown=False)

    def _observe_candidate(self, prediction: Prediction, pose: PoseFrame | None) -> None:
        self._peak_probability = max(self._peak_probability, self.ema_probability or 0.0)
        if (self.ema_probability or 0.0) >= self.config.confirm_fall_probability:
            self._high_confidence_seen = True
        if prediction.posture is PostureLabel.LYING:
            if self._lying_since_ms is None:
                self._lying_since_ms = prediction.timestamp_ms
                self._add_evidence("Posture classifier indicates lying")
        else:
            self._lying_since_ms = None

    def _observe_pose(self, pose: PoseFrame | None, *, candidate_active: bool) -> None:
        if pose is None:
            self._last_motion = None
            self._last_pose = None
            if candidate_active:
                self._inactive_since_ms = None
            return
        current = _pose_observation(pose)
        if current is None:
            self._last_motion = None
            self._last_pose = None
            if candidate_active:
                self._inactive_since_ms = None
            return
        previous = self._last_pose
        self._last_pose = current
        if not candidate_active or previous is None:
            self._last_motion = None
            if not candidate_active:
                self._inactive_since_ms = None
            return
        gap_ms = current.timestamp_ms - previous.timestamp_ms
        if gap_ms < 0 or gap_ms > self.max_pose_gap_ms:
            self._last_motion = None
            self._inactive_since_ms = None
            return
        common = current.valid & previous.valid
        if int(common.sum()) < 3:
            self._last_motion = None
            self._inactive_since_ms = None
            return
        distances = np.linalg.norm(current.xyz[common] - previous.xyz[common], axis=1)
        motion = float(np.median(distances))
        self._last_motion = motion
        if motion <= self.motion_threshold:
            if self._inactive_since_ms is None:
                self._inactive_since_ms = previous.timestamp_ms
        else:
            self._inactive_since_ms = current.timestamp_ms

    def _is_upright(self, prediction: Prediction, pose: PoseFrame | None) -> bool:
        if prediction.posture is not PostureLabel.UPRIGHT:
            return False
        if pose is None:
            return True
        valid = pose.valid
        landmarks = pose.landmarks
        key_indices = np.array([11, 12, 23, 24])
        if not bool(np.all(valid[key_indices])):
            return True
        shoulders = landmarks[[11, 12], :2].mean(axis=0)
        hips = landmarks[[23, 24], :2].mean(axis=0)
        torso = shoulders - hips
        torso_upright = abs(float(torso[1])) >= abs(float(torso[0])) * 1.15
        usable = landmarks[valid, :2]
        if usable.shape[0] < 5:
            return torso_upright
        width = float(np.ptp(usable[:, 0]))
        height = float(np.ptp(usable[:, 1]))
        aspect_upright = height >= max(width * 1.05, 1e-6)
        return torso_upright and aspect_upright

    def _make_update(self, prediction: Prediction, previous_state: SystemState) -> EventUpdate:
        risk = {
            SystemState.NORMAL: RiskLevel.LOW,
            SystemState.POSSIBLE_FALL: RiskLevel.MEDIUM,
            SystemState.VERIFYING: RiskLevel.MEDIUM,
            SystemState.CONFIRMED_FALL: RiskLevel.HIGH,
            SystemState.RECOVERED: RiskLevel.LOW,
        }[self.state]
        lying_seconds = self._elapsed_since(self._lying_since_ms, prediction.timestamp_ms)
        upright_seconds = self._elapsed_since(self._upright_since_ms, prediction.timestamp_ms)
        return EventUpdate(
            state=self.state,
            risk_level=risk,
            timestamp_ms=prediction.timestamp_ms,
            event_id=self.event_id,
            inactive_seconds=self.inactive_seconds,
            self_recovery=self.state is SystemState.RECOVERED,
            evidence=list(self._evidence),
            metadata={
                "raw_fall_probability": float(prediction.fall_probability),
                "smoothed_fall_probability": float(self.ema_probability or 0.0),
                "peak_fall_probability": float(self._peak_probability),
                "posture": prediction.posture.name,
                "motion": self._last_motion,
                "lying_seconds": lying_seconds,
                "upright_seconds": upright_seconds,
                "confirmed_at_ms": self._confirmed_at_ms,
                "previous_state": previous_state.value,
                "state_changed": previous_state is not self.state,
                "just_confirmed": (
                    previous_state is not SystemState.CONFIRMED_FALL
                    and self.state is SystemState.CONFIRMED_FALL
                ),
            },
        )

    def _clear_candidate(self, *, keep_cooldown: bool) -> None:
        self._candidate_since_ms = None
        self._possible_since_ms = None
        self._lying_since_ms = None
        self._upright_since_ms = None
        self._inactive_since_ms = None
        self._peak_probability = 0.0
        self._high_confidence_seen = False
        self._evidence.clear()
        if not keep_cooldown:
            self._confirmed_at_ms = None
            self.event_id = None

    def _add_evidence(self, message: str) -> None:
        if message not in self._evidence:
            self._evidence.append(message)

    @staticmethod
    def _elapsed_since(start_ms: int | None, timestamp_ms: int) -> float:
        if start_ms is None:
            return 0.0
        return max(0.0, (timestamp_ms - start_ms) / 1000.0)


EventStateMachine = FallStateMachine


def _pose_observation(pose: PoseFrame | None) -> _PoseObservation | None:
    if pose is None:
        return None
    valid = np.asarray(pose.valid, dtype=bool)
    finite = np.isfinite(pose.landmarks[:, :3]).all(axis=1)
    valid = valid & finite
    if int(valid.sum()) < 3:
        return None
    return _PoseObservation(
        timestamp_ms=int(pose.timestamp_ms),
        xyz=np.asarray(pose.landmarks[:, :3], dtype=np.float32).copy(),
        valid=valid.copy(),
    )


def _new_event_id(timestamp_ms: int) -> str:
    return f"FALL-{timestamp_ms:013d}-{uuid4().hex[:8].upper()}"
