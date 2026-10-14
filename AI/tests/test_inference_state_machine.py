from __future__ import annotations

import numpy as np
import pytest

from fallguard.config import EventDetectionConfig
from fallguard.domain import PoseFrame, PostureLabel, Prediction, SystemState
from fallguard.inference import CausalSequenceBuffer, DummyClassifier, FallStateMachine


def make_pose(frame_index: int, timestamp_ms: int, *, offset: float = 0.0) -> PoseFrame:
    landmarks = np.zeros((33, 4), dtype=np.float32)
    landmarks[:, 0] = np.linspace(0.3, 0.7, 33) + offset
    landmarks[:, 1] = np.linspace(0.2, 0.8, 33)
    landmarks[:, 3] = 1.0
    return PoseFrame(
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        landmarks=landmarks,
        valid=np.ones(33, dtype=bool),
    )


def prediction(
    timestamp_ms: int,
    posture: PostureLabel,
    fall_probability: float,
) -> Prediction:
    probabilities = {
        PostureLabel.UPRIGHT: (0.95, 0.03, 0.02),
        PostureLabel.TRANSITION: (0.03, 0.95, 0.02),
        PostureLabel.LYING: (0.02, 0.03, 0.95),
    }[posture]
    return Prediction(
        posture=posture,
        posture_probabilities=probabilities,
        fall_probability=fall_probability,
        timestamp_ms=timestamp_ms,
        model_version="test",
    )


def test_causal_buffer_readiness_and_order() -> None:
    buffer = CausalSequenceBuffer(window_size=3, stride=2)
    assert buffer.append(make_pose(0, 0)) is False
    assert buffer.append(make_pose(1, 50)) is False
    assert buffer.append(make_pose(2, 100)) is True
    assert [frame.frame_index for frame in buffer.window()] == [0, 1, 2]
    assert buffer.append(make_pose(3, 150)) is False
    assert buffer.append(make_pose(4, 200)) is True
    assert [frame.frame_index for frame in buffer.window()] == [2, 3, 4]
    with pytest.raises(ValueError, match="frame_index"):
        buffer.append(make_pose(4, 250))


def test_dummy_classifier_is_safe_and_targets_last_frame() -> None:
    frames = [make_pose(index, index * 50) for index in range(4)]
    result = DummyClassifier().predict(frames)
    assert result.posture is PostureLabel.UPRIGHT
    assert result.fall_probability == 0.0
    assert result.timestamp_ms == 150


def test_timestamp_state_machine_confirms_recovers_and_deduplicates() -> None:
    config = EventDetectionConfig(
        ema_alpha=1.0,
        possible_fall_probability=0.6,
        confirm_fall_probability=0.8,
        possible_fall_min_seconds=0.1,
        fallen_min_seconds=0.2,
        inactive_seconds=0.3,
        recovery_upright_seconds=0.2,
        cooldown_seconds=0.5,
    )
    machine = FallStateMachine(config, motion_threshold=0.01)

    first = machine.update(
        prediction(0, PostureLabel.TRANSITION, 0.95),
        make_pose(0, 0),
    )
    assert first.state is SystemState.POSSIBLE_FALL
    verifying = machine.update(
        prediction(100, PostureLabel.TRANSITION, 0.95),
        make_pose(1, 100),
    )
    assert verifying.state is SystemState.VERIFYING

    machine.update(prediction(200, PostureLabel.LYING, 0.95), make_pose(2, 200))
    machine.update(prediction(300, PostureLabel.LYING, 0.95), make_pose(3, 300))
    confirmed = machine.update(
        prediction(400, PostureLabel.LYING, 0.95),
        make_pose(4, 400),
    )
    assert confirmed.state is SystemState.CONFIRMED_FALL
    assert confirmed.risk_level.value == "HIGH"
    assert confirmed.inactive_seconds == pytest.approx(0.4)
    assert confirmed.event_id is not None
    assert confirmed.metadata["just_confirmed"] is True

    same_event = machine.update(
        prediction(500, PostureLabel.LYING, 0.95),
        make_pose(5, 500),
    )
    assert same_event.event_id == confirmed.event_id
    assert same_event.metadata["just_confirmed"] is False

    machine.update(prediction(600, PostureLabel.UPRIGHT, 0.05), None)
    recovered = machine.update(prediction(800, PostureLabel.UPRIGHT, 0.05), None)
    assert recovered.state is SystemState.RECOVERED
    assert recovered.self_recovery is True
    assert recovered.event_id == confirmed.event_id

    normal = machine.update(prediction(900, PostureLabel.UPRIGHT, 0.05), None)
    assert normal.state is SystemState.NORMAL
    assert normal.event_id is None


def test_state_machine_rejects_backward_time() -> None:
    machine = FallStateMachine()
    machine.update(prediction(100, PostureLabel.UPRIGHT, 0.0))
    with pytest.raises(ValueError, match="monotonically"):
        machine.update(prediction(99, PostureLabel.UPRIGHT, 0.0))


def test_missing_pose_breaks_inactivity_measurement() -> None:
    config = EventDetectionConfig(
        ema_alpha=1.0,
        possible_fall_probability=0.6,
        confirm_fall_probability=0.8,
        possible_fall_min_seconds=0.0,
        fallen_min_seconds=0.0,
        inactive_seconds=0.15,
        recovery_upright_seconds=0.2,
        cooldown_seconds=1.0,
    )
    machine = FallStateMachine(config, motion_threshold=0.01)
    machine.update(
        prediction(0, PostureLabel.TRANSITION, 0.95),
        make_pose(0, 0),
    )
    missing = PoseFrame(
        frame_index=1,
        timestamp_ms=100,
        landmarks=np.zeros((33, 4), dtype=np.float32),
        valid=np.zeros(33, dtype=bool),
    )
    machine.update(prediction(100, PostureLabel.LYING, 0.95), missing)
    after_visibility_returns = machine.update(
        prediction(200, PostureLabel.LYING, 0.95),
        make_pose(2, 200),
    )
    assert after_visibility_returns.inactive_seconds == 0.0

    not_yet_confirmed = machine.update(
        prediction(300, PostureLabel.LYING, 0.95),
        make_pose(3, 300),
    )
    assert not_yet_confirmed.state is SystemState.VERIFYING
    assert not_yet_confirmed.inactive_seconds == pytest.approx(0.1)

    confirmed = machine.update(
        prediction(400, PostureLabel.LYING, 0.95),
        make_pose(4, 400),
    )
    assert confirmed.state is SystemState.CONFIRMED_FALL
