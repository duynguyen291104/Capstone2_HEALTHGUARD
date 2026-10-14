from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from fallguard.domain import PoseFrame
from fallguard.inference import DummyClassifier, InferencePipeline, NullPoseEstimator


class StaticPoseEstimator:
    def __init__(self) -> None:
        self.timestamps_ms: list[int] = []
        self.frame_indices: list[int] = []
        self.frame_shapes: list[tuple[int, ...]] = []

    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        timestamp_ms: int,
    ) -> PoseFrame:
        self.timestamps_ms.append(timestamp_ms)
        self.frame_indices.append(frame_index)
        self.frame_shapes.append(frame_bgr.shape)
        landmarks = np.zeros((33, 4), dtype=np.float32)
        landmarks[:, 0] = 0.5
        landmarks[:, 1] = np.linspace(0.2, 0.8, 33)
        landmarks[:, 3] = 1.0
        return PoseFrame(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            landmarks=landmarks,
            valid=np.ones(33, dtype=bool),
        )


def test_image_sequence_hook_writes_annotations_and_json(tmp_path: Path) -> None:
    pipeline = InferencePipeline(
        classifier=DummyClassifier(),
        pose_estimator=StaticPoseEstimator(),
        window_size=3,
        stride=2,
    )
    frames = [np.zeros((64, 96, 3), dtype=np.uint8) for _ in range(5)]
    output_dir = tmp_path / "annotated"
    events_json = tmp_path / "events.json"
    result = pipeline.run_image_sequence(
        frames,
        fps=10,
        output_dir=output_dir,
        events_json=events_json,
    )

    assert result.frame_count == 5
    assert result.pose_frame_count == 5
    assert result.prediction_count == 2
    assert len(list(output_dir.glob("*.jpg"))) == 5
    payload = json.loads(events_json.read_text(encoding="utf-8"))
    assert payload["prediction_count"] == 2
    assert payload["events"] == []
    assert cv2.imread(str(output_dir / "frame_000000.jpg")) is not None


def test_video_downsamples_30_fps_to_20_and_keeps_output_frames(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    annotated = tmp_path / "annotated.mp4"
    size = (96, 64)
    writer = cv2.VideoWriter(
        str(source),
        cv2.VideoWriter_fourcc(*"mp4v"),
        30.0,
        size,
    )
    assert writer.isOpened()
    for index in range(30):
        frame = np.full((size[1], size[0], 3), index * 4, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    estimator = StaticPoseEstimator()
    pipeline = InferencePipeline(
        classifier=DummyClassifier(),
        pose_estimator=estimator,
        window_size=4,
        stride=1,
    )
    result = pipeline.run_video(
        source,
        output_video=annotated,
        target_fps=20.0,
        crop_right_half=True,
    )

    assert result.frame_count == 30
    assert result.sampled_frame_count == 20
    assert result.pose_frame_count == 20
    assert result.source_fps == pytest.approx(30.0)
    assert result.target_fps == 20.0
    assert len(estimator.timestamps_ms) == 20
    assert estimator.timestamps_ms == list(range(0, 1_000, 50))
    assert estimator.frame_indices == [
        0,
        2,
        3,
        4,
        6,
        8,
        9,
        10,
        12,
        14,
        15,
        16,
        18,
        20,
        21,
        22,
        24,
        26,
        27,
        28,
    ]
    assert set(estimator.frame_shapes) == {(64, 48, 3)}
    assert all(
        current > previous
        for previous, current in zip(
            estimator.timestamps_ms,
            estimator.timestamps_ms[1:],
            strict=False,
        )
    )

    capture = cv2.VideoCapture(str(annotated))
    assert capture.isOpened()
    assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 30
    assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(30.0)
    assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == 48
    assert int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 64
    capture.release()


def test_missing_pose_frames_keep_causal_timing_without_counting_as_detected(
    tmp_path: Path,
) -> None:
    pipeline = InferencePipeline(
        classifier=DummyClassifier(),
        pose_estimator=NullPoseEstimator(),
        window_size=4,
        stride=1,
    )
    frames = [np.zeros((48, 64, 3), dtype=np.uint8) for _ in range(6)]

    result = pipeline.run_image_sequence(frames, fps=20.0)

    assert result.frame_count == 6
    assert result.sampled_frame_count == 6
    assert result.pose_frame_count == 0
    assert result.prediction_count == 3
    buffered = pipeline.buffer.available()
    assert [frame.timestamp_ms for frame in buffered] == [100, 150, 200, 250]
    assert all(not frame.valid.any() for frame in buffered)
    assert pipeline.latest_event is not None
    assert pipeline.latest_event.inactive_seconds == 0.0
