from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from fallguard.inference.evidence import extract_video_snapshot, save_snapshot


def test_save_snapshot_validates_and_writes_jpeg(tmp_path: Path) -> None:
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 180
    output = save_snapshot(frame, tmp_path / "event.jpg")

    decoded = cv2.imread(str(output))
    assert decoded is not None
    assert decoded.shape == frame.shape
    assert not output.with_suffix(".jpg.tmp").exists()

    with pytest.raises(ValueError, match="uint8"):
        save_snapshot(frame.astype(np.float32), tmp_path / "invalid.jpg")
    with pytest.raises(ValueError, match=r"\.jpg"):
        save_snapshot(frame, tmp_path / "invalid.png")


def test_extract_video_snapshot_seeks_to_event_time(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(
        str(source),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10.0,
        (64, 48),
    )
    assert writer.isOpened()
    for index in range(10):
        writer.write(np.full((48, 64, 3), index * 20, dtype=np.uint8))
    writer.release()

    output = extract_video_snapshot(
        source,
        tmp_path / "snapshot.jpg",
        timestamp_ms=600,
    )

    frame = cv2.imread(str(output))
    assert frame is not None
    assert float(frame.mean()) == pytest.approx(120, abs=12)
