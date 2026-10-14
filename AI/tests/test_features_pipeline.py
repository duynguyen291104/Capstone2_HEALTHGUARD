from __future__ import annotations

import numpy as np

from fallguard.features.preprocessing import (
    build_frame_features,
    interpolate_short_gaps,
    normalize_landmarks,
)
from fallguard.features.windows import build_causal_windows


def _poses(frames: int = 8) -> tuple[np.ndarray, np.ndarray]:
    landmarks = np.zeros((frames, 33, 4), dtype=np.float32)
    valid = np.ones((frames, 33), dtype=bool)
    landmarks[..., 3] = 0.9
    landmarks[:, 23, :3] = (-1.0, 0.0, 0.0)
    landmarks[:, 24, :3] = (1.0, 0.0, 0.0)
    landmarks[:, 11, :3] = (-1.0, -2.0, 0.0)
    landmarks[:, 12, :3] = (1.0, -2.0, 0.0)
    landmarks[:, 25, :3] = (-1.0, 2.0, 0.0)
    landmarks[:, 26, :3] = (1.0, 2.0, 0.0)
    landmarks[:, 27, :3] = (-1.0, 4.0, 0.0)
    landmarks[:, 28, :3] = (1.0, 4.0, 0.0)
    landmarks[:, 0, :3] = (0.0, -4.0, 0.0)
    landmarks[..., 0] += np.arange(frames)[:, None] * 0.1
    return landmarks, valid


def test_short_gap_interpolation_retains_both_masks() -> None:
    landmarks, valid = _poses(8)
    expected_first = landmarks[1, 5] * (2 / 3) + landmarks[4, 5] * (1 / 3)
    valid[2:4, 5] = False
    landmarks[2:4, 5] = 0.0
    valid[1:6, 6] = False
    landmarks[1:6, 6] = 0.0

    result = interpolate_short_gaps(landmarks, valid, max_gap=2)

    assert not result.observed_valid[2:4, 5].any()
    assert result.usable_valid[2:4, 5].all()
    assert result.interpolated[2:4, 5].all()
    assert np.allclose(result.landmarks[2, 5], expected_first)
    assert not result.usable_valid[1:6, 6].any()
    assert np.all(result.landmarks[1:6, 6] == 0)


def test_hip_center_and_torso_scale_normalization() -> None:
    landmarks, valid = _poses(3)
    result = normalize_landmarks(landmarks, valid)
    hip_center = (
        result.landmarks[:, 23, :3] + result.landmarks[:, 24, :3]
    ) / 2
    shoulder_center = (
        result.landmarks[:, 11, :3] + result.landmarks[:, 12, :3]
    ) / 2
    assert np.allclose(hip_center, 0.0)
    assert np.allclose(np.linalg.norm(shoulder_center - hip_center, axis=1), 1.0)
    assert np.allclose(result.scales, 2.0)


def test_feature_dimensions_and_causal_last_frame_targets() -> None:
    landmarks, valid = _poses(50)
    features = build_frame_features(landmarks, valid)
    assert features.values.shape == (50, 239)
    assert features.values.dtype == np.float32

    posture = np.arange(50, dtype=np.int64) % 3
    event = (np.arange(50) >= 42).astype(np.int64)
    windows = build_causal_windows(
        features.values,
        posture,
        event,
        frame_valid=features.frame_valid,
        window_size=40,
        stride=5,
        sequence_id="sequence-1",
    )
    assert windows.start_indices.tolist() == [0, 5, 10]
    assert windows.end_indices.tolist() == [39, 44, 49]
    assert windows.posture_targets.tolist() == posture[[39, 44, 49]].tolist()
    assert windows.event_targets.tolist() == event[[39, 44, 49]].tolist()
    assert np.array_equal(windows.windows[1], features.values[5:45])
    assert windows.sequence_ids.tolist() == ["sequence-1"] * 3
