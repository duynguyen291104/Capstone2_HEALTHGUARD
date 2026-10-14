from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fallguard.data.manifest import SPLIT_NAMES, ManifestEntry, read_manifest
from fallguard.features.preprocessing import build_frame_features
from fallguard.features.windows import CausalWindowBatch, build_causal_windows
from fallguard.pose.prepare import validate_pose_npz


@dataclass(frozen=True, slots=True)
class WindowFileInfo:
    split: str
    path: Path
    num_windows: int
    input_size: int
    window_size: int


@dataclass(frozen=True, slots=True)
class WindowMaterializationReport:
    output_dir: Path
    files: tuple[WindowFileInfo, ...]

    @property
    def total_windows(self) -> int:
        return sum(item.num_windows for item in self.files)

    def path_for(self, split: str) -> Path:
        for item in self.files:
            if item.split == split:
                return item.path
        raise KeyError(split)


@dataclass(frozen=True, slots=True)
class _SequenceWindows:
    batch: CausalWindowBatch
    group_id: str
    camera_id: str
    frame_indices: np.ndarray
    timestamps_ms: np.ndarray
    provenance: str


def _resolve_entries(
    manifest_or_entries: str | Path | Sequence[ManifestEntry],
) -> list[ManifestEntry]:
    if isinstance(manifest_or_entries, (str, Path)):
        path = Path(manifest_or_entries)
        return read_manifest(path, resolve_from=path.parent)
    entries = list(manifest_or_entries)
    if not all(isinstance(entry, ManifestEntry) for entry in entries):
        raise TypeError("manifest_or_entries must contain ManifestEntry values")
    return entries


def _pose_path(entry: ManifestEntry, pose_dir: Path | None) -> Path:
    if entry.path.suffix.lower() == ".npz":
        return entry.path
    if pose_dir is None:
        raise ValueError(
            f"{entry.sequence_id} points to raw media ({entry.path}); pass pose_dir "
            "containing prepared <sequence_id>.npz files"
        )
    return pose_dir / f"{entry.sequence_id}.npz"


def _sequence_windows(
    entry: ManifestEntry,
    pose_path: Path,
    *,
    window_size: int,
    stride: int,
    max_interpolation_gap: int,
    include_xyz: bool,
    include_visibility: bool,
    include_velocity: bool,
    include_geometry: bool,
) -> _SequenceWindows:
    validate_pose_npz(pose_path, expected_sequence_id=entry.sequence_id)
    with np.load(pose_path, allow_pickle=False) as archive:
        landmarks = np.asarray(archive["landmarks"], dtype=np.float32)
        valid = np.asarray(archive["valid"], dtype=bool)
        posture = np.asarray(archive["posture_labels"], dtype=np.int64)
        event = np.asarray(archive["event_labels"], dtype=np.int64)
        frame_indices = np.asarray(archive["frame_indices"], dtype=np.int64)
        timestamps = np.asarray(archive["timestamps_ms"], dtype=np.int64)
        provenance = str(np.asarray(archive["annotation_provenance"]).reshape(-1)[0])
    features = build_frame_features(
        landmarks,
        valid,
        max_interpolation_gap=max_interpolation_gap,
        include_xyz=include_xyz,
        include_visibility=include_visibility,
        include_velocity=include_velocity,
        include_geometry=include_geometry,
    )
    batch = build_causal_windows(
        features.values,
        posture,
        event,
        frame_valid=features.frame_valid,
        window_size=window_size,
        stride=stride,
        sequence_id=entry.sequence_id,
        drop_unknown_targets=True,
    )
    return _SequenceWindows(
        batch=batch,
        group_id=entry.group_id,
        camera_id=entry.camera_id,
        frame_indices=frame_indices,
        timestamps_ms=timestamps,
        provenance=provenance,
    )


def _atomic_save(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_window_datasets(
    manifest_or_entries: str | Path | Sequence[ManifestEntry],
    output_dir: str | Path,
    *,
    pose_dir: str | Path | None = None,
    window_size: int = 40,
    stride: int = 5,
    max_interpolation_gap: int = 3,
    include_xyz: bool = True,
    include_visibility: bool = True,
    include_velocity: bool = True,
    include_geometry: bool = True,
    required_splits: Sequence[str] = ("train", "validation", "test"),
) -> WindowMaterializationReport:
    """Create one consolidated, training-ready causal-window NPZ per split."""

    if window_size < 1 or stride < 1:
        raise ValueError("window_size and stride must be positive")
    entries = _resolve_entries(manifest_or_entries)
    if not entries:
        raise ValueError("Manifest contains no sequences")
    requested = tuple(required_splits)
    if any(split not in SPLIT_NAMES for split in requested):
        raise ValueError(f"required_splits must be drawn from {SPLIT_NAMES}")
    resolved_pose_dir = Path(pose_dir) if pose_dir is not None else None
    by_split: dict[str, list[_SequenceWindows]] = {
        split: [] for split in SPLIT_NAMES
    }
    for entry in entries:
        windows = _sequence_windows(
            entry,
            _pose_path(entry, resolved_pose_dir),
            window_size=window_size,
            stride=stride,
            max_interpolation_gap=max_interpolation_gap,
            include_xyz=include_xyz,
            include_visibility=include_visibility,
            include_velocity=include_velocity,
            include_geometry=include_geometry,
        )
        if len(windows.batch):
            by_split[entry.split].append(windows)

    output = Path(output_dir)
    infos: list[WindowFileInfo] = []
    for split in SPLIT_NAMES:
        sequences = by_split[split]
        if not sequences:
            if split in requested:
                raise ValueError(
                    f"Split {split!r} contains no usable windows. Increase the "
                    "number/length of sequences or adjust the group split."
                )
            continue
        feature_shapes = {
            sequence.batch.windows.shape[1:] for sequence in sequences
        }
        if len(feature_shapes) != 1:
            raise ValueError(
                f"Split {split!r} contains incompatible window shapes: "
                f"{sorted(feature_shapes)}"
            )

        features = np.concatenate(
            [sequence.batch.windows for sequence in sequences], axis=0
        ).astype(np.float32, copy=False)
        posture = np.concatenate(
            [sequence.batch.posture_targets for sequence in sequences]
        ).astype(np.int64, copy=False)
        event = np.concatenate(
            [sequence.batch.event_targets for sequence in sequences]
        ).astype(np.int64, copy=False)
        validity = np.concatenate(
            [sequence.batch.validity for sequence in sequences], axis=0
        )
        starts = np.concatenate(
            [sequence.batch.start_indices for sequence in sequences]
        )
        ends = np.concatenate([sequence.batch.end_indices for sequence in sequences])
        sequence_ids = np.concatenate(
            [sequence.batch.sequence_ids for sequence in sequences]
        )
        group_ids = np.concatenate(
            [
                np.repeat(sequence.group_id, len(sequence.batch))
                for sequence in sequences
            ]
        )
        camera_ids = np.concatenate(
            [
                np.repeat(sequence.camera_id, len(sequence.batch))
                for sequence in sequences
            ]
        )
        provenances = np.concatenate(
            [
                np.repeat(sequence.provenance, len(sequence.batch))
                for sequence in sequences
            ]
        )
        target_frame_indices = np.concatenate(
            [
                sequence.frame_indices[sequence.batch.end_indices]
                for sequence in sequences
            ]
        ).astype(np.int64, copy=False)
        target_timestamps = np.concatenate(
            [
                sequence.timestamps_ms[sequence.batch.end_indices]
                for sequence in sequences
            ]
        ).astype(np.int64, copy=False)

        path = output / f"windows_{split}.npz"
        _atomic_save(
            path,
            features=features,
            posture_targets=posture,
            event_targets=event,
            validity=validity,
            start_indices=starts.astype(np.int64, copy=False),
            end_indices=ends.astype(np.int64, copy=False),
            target_indices=ends.astype(np.int64, copy=False),
            sequence_ids=sequence_ids,
            group_ids=group_ids,
            camera_ids=camera_ids,
            frame_indices=target_frame_indices,
            timestamps_ms=target_timestamps,
            annotation_provenance=provenances,
        )
        infos.append(
            WindowFileInfo(
                split=split,
                path=path,
                num_windows=len(features),
                input_size=features.shape[2],
                window_size=features.shape[1],
            )
        )
    return WindowMaterializationReport(output, tuple(infos))


# CLI-oriented alias.
prepare_window_datasets = materialize_window_datasets
