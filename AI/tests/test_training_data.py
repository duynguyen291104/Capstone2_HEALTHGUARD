from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from fallguard.training import WindowDataset, load_window_dataset


def test_load_consolidated_window_npz_and_take_causal_target(tmp_path: Path) -> None:
    features = np.arange(4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    posture_frames = np.tile(np.asarray([0, 0, 1, 1, 2]), (4, 1))
    event_frames = np.tile(np.asarray([0, 0, 0, 1, 1]), (4, 1))
    path = tmp_path / "windows_train.npz"
    np.savez_compressed(
        path,
        features=features,
        posture_labels=posture_frames,
        event_labels=event_frames,
        sequence_ids=np.asarray(["a", "b", "c", "d"]),
        timestamps_ms=np.tile(np.arange(5) * 50, (4, 1)),
    )

    dataset = load_window_dataset(path)

    assert isinstance(dataset, WindowDataset)
    assert dataset.input_size == 3
    assert dataset.window_size == 5
    assert dataset.posture_labels.tolist() == [2, 2, 2, 2]
    assert dataset.event_labels.tolist() == [1.0, 1.0, 1.0, 1.0]
    assert dataset.metadata["timestamp_ms"].tolist() == [200, 200, 200, 200]


def test_load_raw_pose_npz_creates_causal_windows(tmp_path: Path) -> None:
    frame_count = 10
    path = tmp_path / "fall-01.npz"
    np.savez_compressed(
        path,
        landmarks=np.zeros((frame_count, 33, 4), dtype=np.float32),
        valid=np.ones((frame_count, 33), dtype=bool),
        timestamps_ms=np.arange(frame_count, dtype=np.int64) * 50,
        frame_indices=np.arange(frame_count, dtype=np.int64),
        posture_labels=np.arange(frame_count, dtype=np.int64) % 3,
        event_labels=(np.arange(frame_count) >= 6).astype(np.int64),
        annotation_provenance=np.asarray("urfd_depth_feature_derived"),
    )

    dataset = load_window_dataset(path, window_size=4, stride=3)

    assert len(dataset) == 3
    assert tuple(dataset.features.shape) == (3, 4, 132)
    assert dataset.posture_labels.tolist() == [0, 0, 0]
    assert dataset.event_labels.tolist() == [0.0, 1.0, 1.0]
    assert dataset.metadata["target_index"].tolist() == [3, 6, 9]
    assert dataset.metadata["timestamp_ms"].tolist() == [150, 300, 450]


def test_csv_window_index_reads_sequence_slices(tmp_path: Path) -> None:
    sequence_path = tmp_path / "sequence.npz"
    np.savez_compressed(
        sequence_path,
        landmarks=np.zeros((8, 33, 4), dtype=np.float32),
        posture_labels=np.asarray([0, 0, 1, 1, 1, 2, 2, 2]),
        event_labels=np.asarray([0, 0, 0, 1, 1, 1, 0, 0]),
        timestamps_ms=np.arange(8) * 50,
    )
    index_path = tmp_path / "windows.csv"
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("npz_path", "start_index", "end_index", "split", "sequence_id"),
        )
        writer.writeheader()
        writer.writerow(
            {
                "npz_path": sequence_path.name,
                "start_index": 0,
                "end_index": 3,
                "split": "train",
                "sequence_id": "s1",
            }
        )
        writer.writerow(
            {
                "npz_path": sequence_path.name,
                "start_index": 4,
                "end_index": 7,
                "split": "validation",
                "sequence_id": "s1",
            }
        )

    dataset = load_window_dataset(index_path, split="train")

    assert len(dataset) == 1
    assert tuple(dataset.features.shape) == (1, 4, 132)
    assert dataset.posture_labels.item() == 1
    assert dataset.event_labels.item() == 1


def test_sequence_manifest_builds_preprocessed_windows_for_requested_split(
    tmp_path: Path,
) -> None:
    pose_dir = tmp_path / "poses"
    pose_dir.mkdir()
    for sequence_id, event_value in (("train-seq", 1), ("val-seq", 0)):
        landmarks = np.zeros((8, 33, 4), dtype=np.float32)
        landmarks[..., 3] = 1.0
        landmarks[:, 11, :2] = (-0.2, -0.3)
        landmarks[:, 12, :2] = (0.2, -0.3)
        landmarks[:, 23, :2] = (-0.1, 0.0)
        landmarks[:, 24, :2] = (0.1, 0.0)
        np.savez_compressed(
            pose_dir / f"{sequence_id}.npz",
            landmarks=landmarks,
            valid=np.ones((8, 33), dtype=bool),
            posture_labels=np.arange(8) % 3,
            event_labels=np.repeat(event_value, 8),
            timestamps_ms=np.arange(8) * 50,
            frame_indices=np.arange(1, 9),
            annotation_provenance=np.asarray("synthetic"),
        )
    manifest = tmp_path / "sequences.csv"
    fieldnames = (
        "dataset",
        "sequence_id",
        "group_id",
        "camera_id",
        "path",
        "num_frames",
        "fps",
        "sequence_label",
        "annotation_path",
        "split",
    )
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sequence_id, split in (("train-seq", "train"), ("val-seq", "validation")):
            writer.writerow(
                {
                    "dataset": "synthetic",
                    "sequence_id": sequence_id,
                    "group_id": sequence_id,
                    "camera_id": "cam0",
                    "path": f"poses/{sequence_id}.npz",
                    "num_frames": 8,
                    "fps": 20,
                    "sequence_label": "fall",
                    "annotation_path": "",
                    "split": split,
                }
            )

    dataset = load_window_dataset(
        manifest,
        split="train",
        window_size=4,
        stride=2,
        feature_config={
            "include_xyz": True,
            "include_visibility": False,
            "include_velocity": False,
            "include_geometry": False,
        },
    )

    assert tuple(dataset.features.shape) == (3, 4, 99)
    assert set(dataset.metadata["sequence_id"]) == {"train-seq"}
    assert dataset.metadata["target_index"].tolist() == [3, 5, 7]
    assert dataset.metadata["frame_index"].tolist() == [4, 6, 8]


def test_dataset_rejects_non_finite_features() -> None:
    features = np.zeros((2, 3, 4), dtype=np.float32)
    features[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        WindowDataset(features, [0, 1], [0, 1])
