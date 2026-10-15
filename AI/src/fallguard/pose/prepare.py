from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fallguard.data.urfd import (
    IGNORE_LABEL,
    derive_annotations,
    parse_urfd_feature_csv,
)
from fallguard.domain.types import SequenceRecord
from fallguard.pose.mediapipe_estimator import PoseEstimator

POSE_NPZ_KEYS = (
    "landmarks",
    "valid",
    "timestamps_ms",
    "frame_indices",
    "posture_labels",
    "event_labels",
    "annotation_provenance",
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


@dataclass(frozen=True, slots=True)
class _SourceFrame:
    frame_number: int
    source_position: int
    image_path: Path | None


@dataclass(frozen=True, slots=True)
class PoseArchiveInfo:
    path: Path
    num_frames: int
    sequence_id: str | None
    provenance: str


@dataclass(frozen=True, slots=True)
class PosePreparationResult:
    sequence_id: str
    output_path: Path
    num_frames: int
    detected_frames: int
    resumed_from: int
    skipped: bool


def _scalar_string(value: np.ndarray) -> str:
    array = np.asarray(value)
    if array.size != 1:
        raise ValueError("annotation_provenance must be a scalar string")
    return str(array.reshape(-1)[0])


def validate_pose_npz(
    path: str | Path,
    *,
    expected_sequence_id: str | None = None,
    expected_frames: int | None = None,
) -> PoseArchiveInfo:
    """Validate the documented pose-sequence NPZ contract without pickle."""

    source = Path(path)
    try:
        with np.load(source, allow_pickle=False) as archive:
            missing = [key for key in POSE_NPZ_KEYS if key not in archive]
            if missing:
                raise ValueError(f"Missing NPZ keys: {missing}")
            landmarks = archive["landmarks"]
            valid = archive["valid"]
            timestamps = archive["timestamps_ms"]
            frame_indices = archive["frame_indices"]
            posture = archive["posture_labels"]
            event = archive["event_labels"]
            provenance = _scalar_string(archive["annotation_provenance"])
            sequence_id = (
                _scalar_string(archive["sequence_id"])
                if "sequence_id" in archive
                else None
            )
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid pose archive {source}: {exc}") from exc

    if landmarks.ndim != 3 or landmarks.shape[1:] != (33, 4):
        raise ValueError(f"{source}: landmarks must have shape [T, 33, 4]")
    frames = len(landmarks)
    if valid.shape != (frames, 33):
        raise ValueError(f"{source}: valid must have shape [T, 33]")
    for name, value in (
        ("timestamps_ms", timestamps),
        ("frame_indices", frame_indices),
        ("posture_labels", posture),
        ("event_labels", event),
    ):
        if value.shape != (frames,):
            raise ValueError(f"{source}: {name} must have shape [T]")
    if landmarks.dtype != np.float32:
        raise ValueError(f"{source}: landmarks must be float32")
    if valid.dtype != np.bool_:
        raise ValueError(f"{source}: valid must be bool")
    if any(
        value.dtype != np.int64
        for value in (timestamps, frame_indices, posture, event)
    ):
        raise ValueError(f"{source}: timestamps, indices, and labels must be int64")
    if not np.isfinite(landmarks).all():
        raise ValueError(f"{source}: landmarks contain NaN or infinity")
    if frames > 1 and (np.diff(timestamps) <= 0).any():
        raise ValueError(f"{source}: timestamps_ms must be strictly increasing")
    if frames > 1 and (np.diff(frame_indices) <= 0).any():
        raise ValueError(f"{source}: frame_indices must be strictly increasing")
    if not np.isin(posture, [IGNORE_LABEL, 0, 1, 2]).all():
        raise ValueError(f"{source}: invalid posture label")
    if not np.isin(event, [0, 1]).all():
        raise ValueError(f"{source}: event_labels must contain only 0 or 1")
    if not provenance:
        raise ValueError(f"{source}: annotation_provenance cannot be empty")
    if expected_frames is not None and frames != expected_frames:
        raise ValueError(
            f"{source}: contains {frames} frames, expected {expected_frames}"
        )
    if expected_sequence_id is not None and sequence_id != expected_sequence_id:
        raise ValueError(
            f"{source}: sequence_id is {sequence_id!r}, expected {expected_sequence_id!r}"
        )
    return PoseArchiveInfo(source, frames, sequence_id, provenance)


def _frame_number(path: Path, fallback: int) -> int:
    values = re.findall(r"\d+", path.stem)
    return int(values[-1]) if values else fallback


def list_image_frames(path: str | Path) -> list[tuple[int, Path]]:
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"Image sequence directory not found: {root}")
    images = sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and item.suffix.lower() in _IMAGE_SUFFIXES
        ),
        key=lambda item: item.as_posix(),
    )
    numbered = [(_frame_number(image, index), image) for index, image in enumerate(images)]
    numbered.sort(key=lambda item: (item[0], item[1].as_posix()))
    indices = [item[0] for item in numbered]
    if len(indices) != len(set(indices)):
        duplicates = sorted({value for value in indices if indices.count(value) > 1})
        raise ValueError(f"Duplicate frame numbers under {root}: {duplicates[:5]}")
    return numbered


def _resample_positions(
    frame_count: int, source_fps: float, target_fps: float | None
) -> tuple[np.ndarray, float]:
    if source_fps <= 0:
        raise ValueError("source fps must be positive")
    if target_fps is None:
        target_fps = source_fps
    if target_fps <= 0:
        raise ValueError("target_fps must be positive")
    if frame_count == 0:
        return np.empty(0, dtype=np.int64), min(target_fps, source_fps)
    if target_fps >= source_fps:
        return np.arange(frame_count, dtype=np.int64), source_fps
    output_count = int(np.floor((frame_count - 1) * target_fps / source_fps)) + 1
    positions = np.rint(
        np.arange(output_count, dtype=np.float64) * source_fps / target_fps
    ).astype(np.int64)
    positions = np.unique(np.clip(positions, 0, frame_count - 1))
    return positions, target_fps


def _source_frames(
    record: SequenceRecord, target_fps: float | None
) -> tuple[list[_SourceFrame], np.ndarray, float]:
    if record.path.is_dir():
        originals = list_image_frames(record.path)
        positions, output_fps = _resample_positions(
            len(originals), record.fps, target_fps
        )
        selected = [
            _SourceFrame(originals[position][0], int(position), originals[position][1])
            for position in positions.tolist()
        ]
    elif record.path.is_file() and record.path.suffix.lower() in {
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
    }:
        if record.num_frames <= 0:
            raise ValueError(f"Video record has no frames: {record.sequence_id}")
        positions, output_fps = _resample_positions(
            record.num_frames, record.fps, target_fps
        )
        selected = [
            _SourceFrame(int(position) + 1, int(position), None)
            for position in positions.tolist()
        ]
    else:
        raise FileNotFoundError(
            f"Sequence source must be an image directory or video: {record.path}"
        )
    timestamps = np.rint(
        np.arange(len(selected), dtype=np.float64) * 1000.0 / output_fps
    ).astype(np.int64)
    return selected, timestamps, output_fps


def _atomic_savez(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _archive_arrays(
    *,
    record: SequenceRecord,
    landmarks: np.ndarray,
    valid: np.ndarray,
    timestamps_ms: np.ndarray,
    frame_indices: np.ndarray,
    posture_labels: np.ndarray,
    event_labels: np.ndarray,
    provenance: str,
    output_fps: float | None = None,
    processed_count: int | None = None,
) -> dict[str, np.ndarray]:
    arrays = {
        "landmarks": np.asarray(landmarks, dtype=np.float32),
        "valid": np.asarray(valid, dtype=bool),
        "timestamps_ms": np.asarray(timestamps_ms, dtype=np.int64),
        "frame_indices": np.asarray(frame_indices, dtype=np.int64),
        "posture_labels": np.asarray(posture_labels, dtype=np.int64),
        "event_labels": np.asarray(event_labels, dtype=np.int64),
        "annotation_provenance": np.asarray(provenance),
        "dataset": np.asarray(record.dataset),
        "sequence_id": np.asarray(record.sequence_id),
        "group_id": np.asarray(record.group_id),
        "camera_id": np.asarray(record.camera_id),
        "fps": np.asarray(output_fps or record.fps, dtype=np.float64),
    }
    if processed_count is not None:
        arrays["processed_count"] = np.asarray(processed_count, dtype=np.int64)
    return arrays


def _load_partial(
    path: Path,
    *,
    record: SequenceRecord,
    frame_indices: np.ndarray,
) -> tuple[int, np.ndarray, np.ndarray] | None:
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as archive:
            if _scalar_string(archive["sequence_id"]) != record.sequence_id:
                return None
            if not np.array_equal(archive["frame_indices"], frame_indices):
                return None
            processed = int(np.asarray(archive["processed_count"]).item())
            landmarks = np.asarray(archive["landmarks"], dtype=np.float32)
            valid = np.asarray(archive["valid"], dtype=bool)
    except (KeyError, OSError, ValueError):
        return None
    if not 0 <= processed <= len(frame_indices):
        return None
    if landmarks.shape != (len(frame_indices), 33, 4) or valid.shape != (
        len(frame_indices),
        33,
    ):
        return None
    return processed, landmarks, valid


def prepare_sequence(
    record: SequenceRecord,
    output_path: str | Path,
    estimator: PoseEstimator,
    *,
    feature_rows: pd.DataFrame | None = None,
    checkpoint_every: int = 25,
    post_impact_hold_frames: int = 20,
    target_fps: float | None = 20.0,
    crop_preview_rgb: bool = True,
    overwrite: bool = False,
) -> PosePreparationResult:
    """Extract one image sequence into the documented restartable NPZ format."""

    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be positive")
    destination = Path(output_path)
    if destination.suffix.lower() != ".npz":
        destination = destination / f"{record.sequence_id}.npz"
    frames, timestamps, output_fps = _source_frames(record, target_fps)
    if not frames:
        raise ValueError(f"No frames found for {record.sequence_id}: {record.path}")
    frame_indices = np.asarray([item.frame_number for item in frames], dtype=np.int64)
    if len(timestamps) > 1 and (np.diff(timestamps) <= 0).any():
        raise ValueError("Computed timestamps are not strictly increasing")

    if destination.is_file() and not overwrite:
        info = validate_pose_npz(
            destination,
            expected_sequence_id=record.sequence_id,
            expected_frames=len(frames),
        )
        with np.load(destination, allow_pickle=False) as existing:
            detected = int(np.asarray(existing["valid"]).any(axis=1).sum())
        return PosePreparationResult(
            record.sequence_id,
            destination,
            info.num_frames,
            detected,
            len(frames),
            True,
        )

    if feature_rows is None:
        if record.annotation_path is None:
            raise ValueError(f"{record.sequence_id} has no annotation_path")
        feature_rows = parse_urfd_feature_csv(record.annotation_path)
    annotations = derive_annotations(
        feature_rows,
        record.sequence_id,
        frame_indices,
        post_impact_hold_frames=post_impact_hold_frames,
    )

    partial = destination.with_suffix(f"{destination.suffix}.partial")
    loaded = None if overwrite else _load_partial(
        partial, record=record, frame_indices=frame_indices
    )
    if loaded is None:
        resumed_from = 0
        landmarks = np.zeros((len(frames), 33, 4), dtype=np.float32)
        valid = np.zeros((len(frames), 33), dtype=bool)
    else:
        resumed_from, landmarks, valid = loaded

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to read image sequences") from exc

    capture = None
    current_video_position = 0
    if record.path.is_file():
        capture = cv2.VideoCapture(str(record.path))
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"OpenCV could not open video: {record.path}")
        if resumed_from < len(frames):
            current_video_position = frames[resumed_from].source_position
            capture.set(cv2.CAP_PROP_POS_FRAMES, current_video_position)

    try:
        for position in range(resumed_from, len(frames)):
            source_frame = frames[position]
            if source_frame.image_path is not None:
                image = cv2.imread(str(source_frame.image_path), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError(
                        f"OpenCV could not read image: {source_frame.image_path}"
                    )
            else:
                assert capture is not None
                image = None
                while current_video_position <= source_frame.source_position:
                    ok, candidate = capture.read()
                    if not ok or candidate is None:
                        raise ValueError(
                            f"Video ended before frame {source_frame.frame_number}: "
                            f"{record.path}"
                        )
                    if current_video_position == source_frame.source_position:
                        image = candidate
                    current_video_position += 1
                assert image is not None
                if crop_preview_rgb:
                    if image.shape[1] < 2:
                        raise ValueError(f"Preview frame is too narrow: {record.path}")
                    image = image[:, image.shape[1] // 2 :]

            pose = estimator.process(
                image, source_frame.frame_number, int(timestamps[position])
            )
            if pose is not None:
                landmarks[position] = pose.landmarks
                valid[position] = pose.valid
            processed = position + 1
            if processed % checkpoint_every == 0 and processed < len(frames):
                _atomic_savez(
                    partial,
                    **_archive_arrays(
                        record=record,
                        landmarks=landmarks,
                        valid=valid,
                        timestamps_ms=timestamps,
                        frame_indices=frame_indices,
                        posture_labels=annotations.posture_labels,
                        event_labels=annotations.event_labels,
                        provenance=annotations.provenance,
                        output_fps=output_fps,
                        processed_count=processed,
                    ),
                )
    finally:
        if capture is not None:
            capture.release()

    _atomic_savez(
        destination,
        **_archive_arrays(
            record=record,
            landmarks=landmarks,
            valid=valid,
            timestamps_ms=timestamps,
            frame_indices=frame_indices,
            posture_labels=annotations.posture_labels,
            event_labels=annotations.event_labels,
            provenance=annotations.provenance,
            output_fps=output_fps,
        ),
    )
    validate_pose_npz(
        destination,
        expected_sequence_id=record.sequence_id,
        expected_frames=len(frames),
    )
    partial.unlink(missing_ok=True)
    return PosePreparationResult(
        record.sequence_id,
        destination,
        len(frames),
        int(valid.any(axis=1).sum()),
        resumed_from,
        False,
    )


def prepare_sequences(
    records: Sequence[SequenceRecord],
    output_dir: str | Path,
    estimator_factory: Callable[[], PoseEstimator],
    *,
    checkpoint_every: int = 25,
    post_impact_hold_frames: int = 20,
    target_fps: float | None = 20.0,
    crop_preview_rgb: bool = True,
    overwrite: bool = False,
) -> list[PosePreparationResult]:
    """Prepare multiple records, using a fresh VIDEO tracker per sequence."""

    output = Path(output_dir)
    annotation_cache: dict[Path, pd.DataFrame] = {}
    results: list[PosePreparationResult] = []
    for record in records:
        if record.annotation_path is None:
            raise ValueError(f"{record.sequence_id} has no annotation_path")
        annotation_path = record.annotation_path.resolve()
        rows = annotation_cache.get(annotation_path)
        if rows is None:
            rows = parse_urfd_feature_csv(annotation_path)
            annotation_cache[annotation_path] = rows
        estimator = estimator_factory()
        try:
            results.append(
                prepare_sequence(
                    record,
                    output / f"{record.sequence_id}.npz",
                    estimator,
                    feature_rows=rows,
                    checkpoint_every=checkpoint_every,
                    post_impact_hold_frames=post_impact_hold_frames,
                    target_fps=target_fps,
                    crop_preview_rgb=crop_preview_rgb,
                    overwrite=overwrite,
                )
            )
        finally:
            estimator.close()
    return results


# Public name used by orchestration code.
prepare_image_sequences = prepare_sequences
