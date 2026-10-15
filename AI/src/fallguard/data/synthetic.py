from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from fallguard.data.manifest import ManifestEntry, build_manifest, write_manifest
from fallguard.domain.enums import PostureLabel
from fallguard.domain.types import SequenceRecord

if TYPE_CHECKING:
    from fallguard.features.materialize import WindowMaterializationReport

SYNTHETIC_PROVENANCE = "synthetic_v1"


@dataclass(frozen=True, slots=True)
class SyntheticDatasetReport:
    root: Path
    manifest_path: Path
    entries: tuple[ManifestEntry, ...]
    window_report: WindowMaterializationReport | None = None

    @property
    def sequence_paths(self) -> tuple[Path, ...]:
        return tuple(entry.path for entry in self.entries)


def _base_skeleton() -> np.ndarray:
    """Return a simple 33-point MediaPipe-shaped normalized skeleton."""

    pose = np.zeros((33, 3), dtype=np.float32)
    # Default unused face/hand detail around the head/limb it belongs to.
    pose[:, 0] = np.linspace(-0.025, 0.025, 33, dtype=np.float32)
    pose[:, 1] = np.linspace(-0.34, 0.34, 33, dtype=np.float32)
    pose[:, 2] = np.linspace(-0.015, 0.015, 33, dtype=np.float32)
    pose[0] = (0.0, -0.42, -0.02)  # nose
    pose[7] = (-0.06, -0.40, 0.0)
    pose[8] = (0.06, -0.40, 0.0)
    pose[11] = (-0.13, -0.26, 0.0)
    pose[12] = (0.13, -0.26, 0.0)
    pose[13] = (-0.20, -0.06, 0.0)
    pose[14] = (0.20, -0.06, 0.0)
    pose[15] = (-0.23, 0.13, 0.0)
    pose[16] = (0.23, 0.13, 0.0)
    pose[23] = (-0.09, 0.0, 0.0)
    pose[24] = (0.09, 0.0, 0.0)
    pose[25] = (-0.09, 0.30, 0.0)
    pose[26] = (0.09, 0.30, 0.0)
    pose[27] = (-0.09, 0.59, 0.0)
    pose[28] = (0.09, 0.59, 0.0)
    pose[29] = (-0.10, 0.61, -0.04)
    pose[30] = (0.10, 0.61, -0.04)
    pose[31] = (-0.12, 0.64, 0.05)
    pose[32] = (0.12, 0.64, 0.05)
    return pose


def _pose_sequence(
    frames: int,
    *,
    is_fall: bool,
    deliberate_lying: bool,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    base = _base_skeleton()
    landmarks = np.empty((frames, 33, 4), dtype=np.float32)
    valid = np.ones((frames, 33), dtype=bool)
    posture = np.full(frames, int(PostureLabel.UPRIGHT), dtype=np.int64)
    event = np.zeros(frames, dtype=np.int64)
    transition_start = max(5, int(frames * 0.45))
    lying_start = min(frames - 1, transition_start + max(4, int(frames * 0.15)))

    if is_fall or deliberate_lying:
        posture[transition_start:lying_start] = int(PostureLabel.TRANSITION)
        posture[lying_start:] = int(PostureLabel.LYING)
    if is_fall:
        event[transition_start : min(frames, lying_start + 21)] = 1

    for index in range(frames):
        if index < transition_start or not (is_fall or deliberate_lying):
            progress = 0.0
        elif index >= lying_start:
            progress = 1.0
        else:
            progress = (index - transition_start + 1) / (
                lying_start - transition_start + 1
            )
        # Deliberate ADL lying is slower and smoother, while fall sequences add
        # a sharper hip drop. Labels deliberately overlap; event targets do not.
        eased = progress**2 if is_fall else progress * progress * (3 - 2 * progress)
        angle = eased * (np.pi / 2.0)
        cosine, sine = float(np.cos(angle)), float(np.sin(angle))
        xy = base[:, :2].copy()
        rotated = np.empty_like(xy)
        rotated[:, 0] = cosine * xy[:, 0] - sine * xy[:, 1]
        rotated[:, 1] = sine * xy[:, 0] + cosine * xy[:, 1]
        center = np.asarray(
            (
                0.50 + 0.015 * np.sin(index / 8.0),
                0.43 + (0.35 if is_fall else 0.29) * eased,
            ),
            dtype=np.float32,
        )
        coordinates = np.column_stack((rotated + center, base[:, 2]))
        coordinates += rng.normal(0.0, 0.0025, size=coordinates.shape)
        landmarks[index, :, :3] = coordinates.astype(np.float32)
        landmarks[index, :, 3] = 0.96

    # A deterministic bounded dropout exercises interpolation/mask handling.
    gap_start = min(10, max(1, frames // 5))
    gap_end = min(frames, gap_start + 2)
    valid[gap_start:gap_end, 15] = False
    landmarks[gap_start:gap_end, 15] = 0.0
    return landmarks, valid, posture, event


def _write_pose_archive(
    path: Path,
    *,
    sequence_id: str,
    group_id: str,
    fps: float,
    landmarks: np.ndarray,
    valid: np.ndarray,
    posture: np.ndarray,
    event: np.ndarray,
) -> None:
    frames = len(landmarks)
    frame_indices = np.arange(1, frames + 1, dtype=np.int64)
    timestamps = np.rint(np.arange(frames) * 1000.0 / fps).astype(np.int64)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            landmarks=np.asarray(landmarks, dtype=np.float32),
            valid=np.asarray(valid, dtype=bool),
            timestamps_ms=timestamps,
            frame_indices=frame_indices,
            posture_labels=np.asarray(posture, dtype=np.int64),
            event_labels=np.asarray(event, dtype=np.int64),
            annotation_provenance=np.asarray(SYNTHETIC_PROVENANCE),
            dataset=np.asarray("synthetic"),
            sequence_id=np.asarray(sequence_id),
            group_id=np.asarray(group_id),
            camera_id=np.asarray("cam0"),
            fps=np.asarray(fps, dtype=np.float64),
        )
    temporary.replace(path)


def generate_synthetic_dataset(
    root: str | Path,
    *,
    num_sequences: int = 12,
    frames_per_sequence: int = 80,
    fps: float = 20.0,
    seed: int = 42,
    window_size: int = 40,
    stride: int = 5,
    materialize_windows: bool = True,
    overwrite: bool = False,
) -> SyntheticDatasetReport:
    """Create deterministic pose NPZs and a leakage-free manifest offline."""

    if num_sequences < 6:
        raise ValueError("num_sequences must be at least 6 for three-way smoke splits")
    if frames_per_sequence < 40:
        raise ValueError("frames_per_sequence must be at least 40")
    if fps <= 0:
        raise ValueError("fps must be positive")

    destination = Path(root)
    pose_dir = destination / "poses"
    records: list[SequenceRecord] = []
    for index in range(num_sequences):
        is_fall = index % 2 == 0
        kind = "fall" if is_fall else "adl"
        sequence_id = f"synthetic-{kind}-{index + 1:03d}"
        group_id = sequence_id
        path = pose_dir / f"{sequence_id}.npz"
        sequence_seed = np.random.SeedSequence([seed, index, int(is_fall)])
        rng = np.random.default_rng(sequence_seed)
        arrays = _pose_sequence(
            frames_per_sequence,
            is_fall=is_fall,
            deliberate_lying=(not is_fall and index % 4 == 1),
            rng=rng,
        )
        if overwrite or not path.is_file():
            _write_pose_archive(
                path,
                sequence_id=sequence_id,
                group_id=group_id,
                fps=fps,
                landmarks=arrays[0],
                valid=arrays[1],
                posture=arrays[2],
                event=arrays[3],
            )
        records.append(
            SequenceRecord(
                dataset="synthetic",
                sequence_id=sequence_id,
                group_id=group_id,
                camera_id="cam0",
                path=path,
                num_frames=frames_per_sequence,
                fps=fps,
                sequence_label=kind,
                annotation_path=None,
            )
        )

    entries = build_manifest(records, seed=seed, stratify=True)
    manifest_path = destination / "sequences.csv"
    write_manifest(entries, manifest_path, relative_to=destination)
    window_report = None
    if materialize_windows:
        # Local import keeps the low-level generator usable without introducing
        # a package import cycle through fallguard.features.
        from fallguard.features.materialize import materialize_window_datasets

        window_report = materialize_window_datasets(
            entries,
            destination,
            window_size=window_size,
            stride=stride,
        )
    return SyntheticDatasetReport(
        destination, manifest_path, tuple(entries), window_report
    )


# Short CLI-oriented alias.
make_synthetic_dataset = generate_synthetic_dataset
