from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from fallguard.data.urfd import discover_urfd_sequences
from fallguard.domain.types import PoseFrame, SequenceRecord
from fallguard.pose.prepare import prepare_sequence, validate_pose_npz


def _write_annotations(path: Path, sequence_id: str, frames: int) -> None:
    lines = []
    for frame in range(1, frames + 1):
        label = -1 if frame < 3 else (0 if frame < 5 else 1)
        lines.append(f"{sequence_id},{frame},{label},1,2,.5,4,.9,1700,800,.1\n")
    path.write_text("".join(lines), encoding="utf-8")


class _FakeEstimator:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[tuple[int, int, tuple[int, ...]]] = []
        self.fail_after = fail_after
        self.closed = False

    def process(
        self, frame_bgr: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> PoseFrame | None:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("intentional interruption")
        self.calls.append((frame_index, timestamp_ms, frame_bgr.shape))
        landmarks = np.zeros((33, 4), dtype=np.float32)
        landmarks[:, :3] = frame_index / 100.0
        landmarks[:, 3] = 0.95
        return PoseFrame(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            landmarks=landmarks,
            valid=np.ones(33, dtype=bool),
        )

    def close(self) -> None:
        self.closed = True


def test_image_preparation_resamples_resumes_and_is_idempotent(tmp_path: Path) -> None:
    image_dir = tmp_path / "rgb" / "fall-01"
    image_dir.mkdir(parents=True)
    for frame in range(1, 10):
        image = np.full((12, 16, 3), frame, dtype=np.uint8)
        assert cv2.imwrite(str(image_dir / f"fall-01-cam0-rgb-{frame:03d}.png"), image)
    annotations = tmp_path / "falls.csv"
    _write_annotations(annotations, "fall-01", 9)
    record = SequenceRecord(
        dataset="urfd",
        sequence_id="fall-01",
        group_id="fall-01",
        camera_id="cam0",
        path=image_dir,
        num_frames=9,
        fps=30.0,
        sequence_label="fall",
        annotation_path=annotations,
    )
    output = tmp_path / "fall-01.npz"

    interrupted = _FakeEstimator(fail_after=3)
    try:
        prepare_sequence(
            record,
            output,
            interrupted,
            checkpoint_every=2,
            target_fps=20.0,
        )
    except RuntimeError as exc:
        assert "intentional" in str(exc)
    else:
        raise AssertionError("expected intentional extraction interruption")
    assert output.with_suffix(".npz.partial").is_file()

    resumed = _FakeEstimator()
    result = prepare_sequence(
        record, output, resumed, checkpoint_every=2, target_fps=20.0
    )
    assert result.resumed_from == 2
    assert result.num_frames == 6
    assert resumed.calls[0][0] == 4
    info = validate_pose_npz(
        output, expected_sequence_id="fall-01", expected_frames=6
    )
    assert info.provenance == "urfd_depth_feature_derived"
    with np.load(output, allow_pickle=False) as archive:
        assert archive["frame_indices"].tolist() == [1, 3, 4, 5, 7, 9]
        assert archive["timestamps_ms"].tolist() == [0, 50, 100, 150, 200, 250]
        assert float(archive["fps"]) == 20.0

    untouched = _FakeEstimator()
    skipped = prepare_sequence(record, output, untouched, target_fps=20.0)
    assert skipped.skipped
    assert untouched.calls == []


def test_preview_video_uses_only_rgb_right_half(tmp_path: Path) -> None:
    video = tmp_path / "adl-01-cam0.avi"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"MJPG"),
        30.0,
        (640, 240),
    )
    assert writer.isOpened()
    for _ in range(6):
        frame = np.zeros((240, 640, 3), dtype=np.uint8)
        frame[:, :320] = (10, 20, 30)
        frame[:, 320:] = (200, 210, 220)
        writer.write(frame)
    writer.release()
    annotations = tmp_path / "adls.csv"
    _write_annotations(annotations, "adl-01", 6)
    record = SequenceRecord(
        dataset="urfd",
        sequence_id="adl-01",
        group_id="adl-01",
        camera_id="cam0",
        path=video,
        num_frames=6,
        fps=30.0,
        sequence_label="adl",
        annotation_path=annotations,
    )
    estimator = _FakeEstimator()
    output = tmp_path / "adl-01.npz"
    result = prepare_sequence(record, output, estimator, target_fps=20.0)
    assert result.num_frames == 4
    assert all(shape == (240, 320, 3) for _, _, shape in estimator.calls)
    with np.load(output, allow_pickle=False) as archive:
        assert not archive["event_labels"].any()


def test_discovery_can_consistently_prefer_preview_or_rgb(tmp_path: Path) -> None:
    root = tmp_path / "urfd"
    image_dir = root / "rgb" / "adl-01"
    image_dir.mkdir(parents=True)
    assert cv2.imwrite(
        str(image_dir / "adl-01-cam0-rgb-001.png"),
        np.zeros((10, 10, 3), dtype=np.uint8),
    )
    video_dir = root / "videos"
    video_dir.mkdir()
    video = video_dir / "adl-01-cam0.mp4"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        30.0,
        (20, 10),
    )
    assert writer.isOpened()
    writer.write(np.zeros((10, 20, 3), dtype=np.uint8))
    writer.release()
    annotation_dir = root / "annotations"
    annotation_dir.mkdir()
    _write_annotations(annotation_dir / "urfall-cam0-adls.csv", "adl-01", 1)

    automatic = discover_urfd_sequences(root, prefer="auto")
    previews = discover_urfd_sequences(root, prefer="preview")
    images = discover_urfd_sequences(root, prefer="rgb")
    assert len(automatic) == len(previews) == len(images) == 1
    assert automatic[0].path.is_dir()
    assert previews[0].path == video
    assert images[0].path.is_dir()
