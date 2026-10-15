from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

FEATURE_KEYS = ("features", "windows", "X", "x", "sequences")
POSTURE_KEYS = (
    "posture_targets",
    "posture_labels",
    "posture",
    "y_posture",
)
EVENT_KEYS = ("event_targets", "event_labels", "events", "event", "y_event")
PATH_KEYS = ("window_path", "npz_path", "sequence_path", "path")
START_KEYS = ("start_index", "window_start", "start_frame", "start")
END_KEYS = ("end_index", "window_end", "end_frame", "end")
TARGET_KEYS = ("target_index", "target_frame", "label_index")
SEQUENCE_MANIFEST_FIELDS = {
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
}


class WindowDataset(Dataset[dict[str, Any]]):
    """Validated causal windows for the multi-task temporal model."""

    def __init__(
        self,
        features: np.ndarray | torch.Tensor,
        posture_labels: np.ndarray | torch.Tensor | Sequence[int],
        event_labels: np.ndarray | torch.Tensor | Sequence[int | float],
        metadata: Mapping[str, Sequence[Any] | np.ndarray] | None = None,
    ) -> None:
        feature_values = _as_numpy(features, dtype=np.float32)
        if feature_values.ndim < 3:
            raise ValueError(
                f"features must have shape [samples, time, ...features], got {feature_values.shape}"
            )
        if feature_values.shape[0] < 1 or feature_values.shape[1] < 1:
            raise ValueError("WindowDataset cannot be empty and needs at least one timestep")
        feature_values = feature_values.reshape(
            feature_values.shape[0],
            feature_values.shape[1],
            -1,
        )
        if not np.isfinite(feature_values).all():
            raise ValueError("features contain NaN or infinite values")

        sample_count = feature_values.shape[0]
        posture_values = _target_values(posture_labels, sample_count, "posture_labels")
        event_values = _target_values(event_labels, sample_count, "event_labels")
        if np.any(posture_values < 0):
            raise ValueError("posture_labels must be non-negative integer class IDs")
        if not np.all(np.isin(event_values, (0, 1))):
            raise ValueError("event_labels must contain only 0 or 1")

        self.features = torch.from_numpy(np.ascontiguousarray(feature_values))
        self.posture_labels = torch.as_tensor(posture_values, dtype=torch.long)
        self.event_labels = torch.as_tensor(event_values, dtype=torch.float32)
        self.metadata = _normalize_metadata(metadata or {}, sample_count)

    def __len__(self) -> int:
        return int(self.features.shape[0])

    def __getitem__(self, index: int) -> dict[str, Any]:
        sample: dict[str, Any] = {
            "features": self.features[index],
            "posture_label": self.posture_labels[index],
            "event_label": self.event_labels[index],
            "sample_index": int(index),
        }
        for key, values in self.metadata.items():
            sample[key] = _collatable_scalar(values[index])
        return sample

    @property
    def input_size(self) -> int:
        return int(self.features.shape[-1])

    @property
    def window_size(self) -> int:
        return int(self.features.shape[1])

    @property
    def num_posture_classes(self) -> int:
        return int(self.posture_labels.max().item()) + 1

    @classmethod
    def from_npz(
        cls,
        path: str | Path,
        *,
        window_size: int | None = None,
        stride: int = 1,
        sequence_id: str | None = None,
        feature_config: Any = None,
        max_interpolation_gap: int = 3,
    ) -> WindowDataset:
        return _dataset_from_npz(
            Path(path),
            window_size=window_size,
            stride=stride,
            sequence_id=sequence_id,
            feature_config=feature_config,
            max_interpolation_gap=max_interpolation_gap,
        )

    @classmethod
    def from_index(
        cls,
        path: str | Path,
        *,
        split: str | None = None,
        window_size: int = 40,
        stride: int = 5,
        feature_config: Any = None,
        max_interpolation_gap: int = 3,
    ) -> WindowDataset:
        return _dataset_from_index(
            Path(path),
            split=split,
            window_size=window_size,
            stride=stride,
            feature_config=feature_config,
            max_interpolation_gap=max_interpolation_gap,
        )


def load_window_dataset(
    source: (str | Path | WindowDataset | Mapping[str, Any] | tuple[Any, Any, Any] | list[Any]),
    *,
    split: str | None = None,
    window_size: int | None = None,
    stride: int = 1,
    feature_config: Any = None,
    max_interpolation_gap: int = 3,
) -> WindowDataset:
    """Load consolidated windows, a sequence NPZ, or a CSV/JSON window index."""

    if isinstance(source, WindowDataset):
        return source
    if isinstance(source, Mapping):
        features = _first_value(source, FEATURE_KEYS)
        posture = _first_value(source, POSTURE_KEYS)
        event = _first_value(source, EVENT_KEYS)
        metadata = source.get("metadata")
        if metadata is None:
            ignored = set(FEATURE_KEYS + POSTURE_KEYS + EVENT_KEYS)
            aliases = {
                "sequence_ids": "sequence_id",
                "video_ids": "video_id",
                "timestamps_ms": "timestamp_ms",
                "frame_indices": "frame_index",
            }
            sample_count = int(_as_numpy(features).shape[0])
            metadata = {}
            for key, value in source.items():
                if key in ignored or key == "metadata":
                    continue
                array = _as_numpy(value)
                if array.ndim == 0:
                    array = np.repeat(array.item(), sample_count)
                elif array.shape[0] != sample_count:
                    continue
                elif array.ndim > 1:
                    array = array.reshape(sample_count, -1)[:, -1]
                metadata[aliases.get(str(key), str(key))] = array
        return WindowDataset(features, posture, event, metadata=metadata)
    if isinstance(source, (tuple, list)) and len(source) == 3:
        return WindowDataset(source[0], source[1], source[2])

    path = Path(source)
    if path.is_dir():
        path = _resolve_split_file(path, split)
    if not path.exists():
        raise FileNotFoundError(f"Window dataset source not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".npz":
        return _dataset_from_npz(
            path,
            window_size=window_size,
            stride=stride,
            feature_config=feature_config,
            max_interpolation_gap=max_interpolation_gap,
        )
    if suffix in {".csv", ".json", ".jsonl"}:
        return _dataset_from_index(
            path,
            split=split,
            window_size=window_size or 40,
            stride=stride,
            feature_config=feature_config,
            max_interpolation_gap=max_interpolation_gap,
        )
    raise ValueError(f"Unsupported window dataset format: {path.suffix}")


def load_split_datasets(
    processed_dir: str | Path,
    splits: Sequence[str] = ("train", "validation", "test"),
) -> dict[str, WindowDataset]:
    return {split: load_window_dataset(processed_dir, split=split) for split in splits}


def build_dataloader(
    dataset: Dataset[Any],
    *,
    batch_size: int,
    shuffle: bool,
    num_workers: int = 0,
    seed: int = 42,
    device: str | torch.device = "cpu",
) -> DataLoader[Any]:
    generator = torch.Generator()
    generator.manual_seed(seed)
    device_type = torch.device(device).type
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=device_type == "cuda",
        generator=generator,
        worker_init_fn=_seed_worker if num_workers else None,
        persistent_workers=num_workers > 0,
    )


def infer_input_size(source: WindowDataset | np.ndarray | torch.Tensor) -> int:
    if isinstance(source, WindowDataset):
        return source.input_size
    values = _as_numpy(source)
    if values.ndim < 3:
        raise ValueError("Input arrays must have [samples, time, ...features] shape")
    return int(np.prod(values.shape[2:], dtype=np.int64))


def _dataset_from_npz(
    path: Path,
    *,
    window_size: int | None,
    stride: int,
    sequence_id: str | None = None,
    feature_config: Any = None,
    max_interpolation_gap: int = 3,
) -> WindowDataset:
    if not path.is_file():
        raise FileNotFoundError(f"NPZ dataset not found: {path}")
    if stride < 1:
        raise ValueError("stride must be positive")
    with np.load(path, allow_pickle=False) as archive:
        keys = set(archive.files)
        if "landmarks" in keys and not keys.intersection(FEATURE_KEYS):
            arrays = {key: np.asarray(archive[key]) for key in archive.files}
            return _window_pose_sequence(
                arrays,
                path=path,
                window_size=window_size,
                stride=stride,
                sequence_id=sequence_id,
                feature_config=feature_config,
                max_interpolation_gap=max_interpolation_gap,
            )

        feature_key = _first_key(keys, FEATURE_KEYS, "features/windows")
        posture_key = _first_key(keys, POSTURE_KEYS, "posture labels")
        event_key = _first_key(keys, EVENT_KEYS, "event labels")
        features = np.asarray(archive[feature_key])
        if features.ndim < 3:
            raise ValueError(f"{path}: {feature_key} must have [samples, time, ...features] shape")
        sample_count = features.shape[0]
        posture = _targets_from_archive(archive[posture_key], sample_count, posture_key)
        event = _targets_from_archive(archive[event_key], sample_count, event_key)
        metadata = _metadata_from_archive(archive, sample_count)

    metadata.setdefault(
        "source_path",
        np.repeat(str(path.resolve()), sample_count),
    )
    return WindowDataset(features, posture, event, metadata)


def _window_pose_sequence(
    arrays: Mapping[str, np.ndarray],
    *,
    path: Path,
    window_size: int | None,
    stride: int,
    sequence_id: str | None,
    feature_config: Any,
    max_interpolation_gap: int,
) -> WindowDataset:
    required = {"landmarks", "posture_labels", "event_labels"}
    missing = required.difference(arrays)
    if missing:
        raise ValueError(f"{path}: missing required arrays: {sorted(missing)}")
    landmarks = np.asarray(arrays["landmarks"], dtype=np.float32)
    if landmarks.ndim != 3 or landmarks.shape[1:] != (33, 4):
        raise ValueError(f"{path}: landmarks must have shape [time, 33, 4], got {landmarks.shape}")
    frame_count = landmarks.shape[0]
    if window_size is None:
        # A single full-sequence window is unambiguous and useful for tests;
        # production callers pass config.data.window_size (40 by default).
        window_size = frame_count
    if window_size < 1:
        raise ValueError("window_size must be positive")
    if frame_count < window_size:
        raise ValueError(
            f"{path}: sequence has {frame_count} frames, shorter than window_size={window_size}"
        )

    posture_frames = np.asarray(arrays["posture_labels"]).reshape(-1)
    event_frames = np.asarray(arrays["event_labels"]).reshape(-1)
    if posture_frames.shape != (frame_count,) or event_frames.shape != (frame_count,):
        raise ValueError(f"{path}: frame labels must have length {frame_count}")

    candidate_starts = np.arange(
        0,
        frame_count - window_size + 1,
        stride,
        dtype=np.int64,
    )
    candidate_targets = candidate_starts + window_size - 1
    known_targets = posture_frames[candidate_targets] >= 0
    starts = candidate_starts[known_targets]
    targets = starts + window_size - 1
    if not len(starts):
        raise ValueError(f"{path}: no windows end on a known posture target")
    frame_features: np.ndarray = landmarks
    if feature_config is not None:
        if "valid" not in arrays:
            raise ValueError(f"{path}: valid is required for configured feature extraction")
        from fallguard.features.preprocessing import build_frame_features

        extracted = build_frame_features(
            landmarks,
            np.asarray(arrays["valid"], dtype=bool),
            max_interpolation_gap=max_interpolation_gap,
            include_xyz=bool(_feature_value(feature_config, "include_xyz", True)),
            include_visibility=bool(_feature_value(feature_config, "include_visibility", True)),
            include_velocity=bool(_feature_value(feature_config, "include_velocity", True)),
            include_geometry=bool(_feature_value(feature_config, "include_geometry", True)),
        )
        frame_features = extracted.values
    features = np.stack(
        [frame_features[start : start + window_size] for start in starts],
    )
    resolved_sequence_id = sequence_id or path.stem
    metadata: dict[str, np.ndarray] = {
        "sequence_id": np.repeat(resolved_sequence_id, len(starts)),
        "video_id": np.repeat(resolved_sequence_id, len(starts)),
        "start_index": starts,
        "target_index": targets,
        "source_path": np.repeat(str(path.resolve()), len(starts)),
    }
    for key in ("timestamps_ms", "frame_indices"):
        if key in arrays:
            values = np.asarray(arrays[key]).reshape(-1)
            if values.shape != (frame_count,):
                raise ValueError(f"{path}: {key} must have length {frame_count}")
            target_key = "timestamp_ms" if key == "timestamps_ms" else "frame_index"
            metadata[target_key] = values[targets]
    provenance = arrays.get("annotation_provenance")
    if provenance is not None:
        scalar = _npz_scalar_to_string(provenance)
        metadata["annotation_provenance"] = np.repeat(scalar, len(starts))

    dataset = WindowDataset(
        features,
        posture_frames[targets],
        event_frames[targets],
        metadata,
    )
    if feature_config is not None:
        dataset.feature_names = extracted.feature_names
    return dataset


def _dataset_from_index(
    path: Path,
    *,
    split: str | None,
    window_size: int,
    stride: int,
    feature_config: Any,
    max_interpolation_gap: int,
) -> WindowDataset:
    records = _read_index_records(path)
    is_sequence_manifest = bool(records) and SEQUENCE_MANIFEST_FIELDS.issubset(records[0])
    if split is not None:
        normalized_split = _normalize_split(split)
        records = [
            row
            for row in records
            if "split" not in row or _normalize_split(str(row["split"])) == normalized_split
        ]
    if not records:
        raise ValueError(f"No window records found in {path} for split={split!r}")
    if is_sequence_manifest:
        return _dataset_from_sequence_manifest(
            records,
            manifest_path=path,
            window_size=window_size,
            stride=stride,
            feature_config=feature_config,
            max_interpolation_gap=max_interpolation_gap,
        )

    windows: list[np.ndarray] = []
    postures: list[int] = []
    events: list[int] = []
    metadata_rows: list[dict[str, Any]] = []
    cache: dict[Path, dict[str, np.ndarray]] = {}
    for row_number, row in enumerate(records, start=2):
        try:
            window, posture, event, metadata = _window_from_record(
                row,
                index_path=path,
                cache=cache,
            )
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise ValueError(f"{path}:{row_number}: {error}") from error
        windows.append(window)
        postures.append(posture)
        events.append(event)
        metadata_rows.append(metadata)

    try:
        feature_array = np.stack(windows).astype(np.float32, copy=False)
    except ValueError as error:
        shapes = sorted({tuple(window.shape) for window in windows})
        raise ValueError(f"Window index contains inconsistent shapes: {shapes}") from error
    metadata = _transpose_metadata(metadata_rows)
    return WindowDataset(feature_array, postures, events, metadata)


def _dataset_from_sequence_manifest(
    records: Sequence[Mapping[str, Any]],
    *,
    manifest_path: Path,
    window_size: int,
    stride: int,
    feature_config: Any,
    max_interpolation_gap: int,
) -> WindowDataset:
    """Turn sequence-manifest pose archives into causal feature windows."""

    from fallguard.features.preprocessing import build_frame_features
    from fallguard.features.windows import build_causal_windows

    if window_size < 1 or stride < 1:
        raise ValueError("window_size and stride must be positive")
    windows: list[np.ndarray] = []
    postures: list[np.ndarray] = []
    events: list[np.ndarray] = []
    metadata_parts: dict[str, list[np.ndarray]] = {
        "sequence_id": [],
        "video_id": [],
        "group_id": [],
        "camera_id": [],
        "dataset": [],
        "source_path": [],
        "start_index": [],
        "target_index": [],
        "timestamp_ms": [],
        "frame_index": [],
        "annotation_provenance": [],
    }
    expected_feature_names: tuple[str, ...] | None = None
    for row in records:
        data_path = _resolve_record_path(str(row["path"]), manifest_path.parent)
        with np.load(data_path, allow_pickle=False) as archive:
            required = {"landmarks", "valid", "posture_labels", "event_labels"}
            missing = required.difference(archive.files)
            if missing:
                raise ValueError(
                    f"{data_path}: sequence manifest pose NPZ is missing {sorted(missing)}"
                )
            landmarks = np.asarray(archive["landmarks"], dtype=np.float32)
            valid = np.asarray(archive["valid"], dtype=bool)
            posture_labels = np.asarray(archive["posture_labels"], dtype=np.int64)
            event_labels = np.asarray(archive["event_labels"], dtype=np.int64)
            timestamps = (
                np.asarray(archive["timestamps_ms"], dtype=np.int64)
                if "timestamps_ms" in archive.files
                else None
            )
            frame_indices = (
                np.asarray(archive["frame_indices"], dtype=np.int64)
                if "frame_indices" in archive.files
                else None
            )
            provenance = (
                _npz_scalar_to_string(archive["annotation_provenance"])
                if "annotation_provenance" in archive.files
                else ""
            )

        feature_sequence = build_frame_features(
            landmarks,
            valid,
            max_interpolation_gap=max_interpolation_gap,
            include_xyz=bool(_feature_value(feature_config, "include_xyz", True)),
            include_visibility=bool(_feature_value(feature_config, "include_visibility", True)),
            include_velocity=bool(_feature_value(feature_config, "include_velocity", True)),
            include_geometry=bool(_feature_value(feature_config, "include_geometry", True)),
        )
        if (
            expected_feature_names is not None
            and feature_sequence.feature_names != expected_feature_names
        ):
            raise ValueError("Feature configuration changed between manifest sequences")
        expected_feature_names = feature_sequence.feature_names
        sequence_id = str(row["sequence_id"])
        batch = build_causal_windows(
            feature_sequence.values,
            posture_labels,
            event_labels,
            frame_valid=feature_sequence.frame_valid,
            window_size=window_size,
            stride=stride,
            sequence_id=sequence_id,
        )
        if not len(batch):
            continue
        target_indices = batch.end_indices
        count = len(batch)
        windows.append(batch.windows)
        postures.append(batch.posture_targets)
        events.append(batch.event_targets)
        metadata_parts["sequence_id"].append(np.repeat(sequence_id, count))
        metadata_parts["video_id"].append(np.repeat(sequence_id, count))
        metadata_parts["group_id"].append(np.repeat(str(row["group_id"]), count))
        metadata_parts["camera_id"].append(np.repeat(str(row["camera_id"]), count))
        metadata_parts["dataset"].append(np.repeat(str(row["dataset"]), count))
        metadata_parts["source_path"].append(np.repeat(str(data_path.resolve()), count))
        metadata_parts["start_index"].append(batch.start_indices)
        metadata_parts["target_index"].append(target_indices)
        if timestamps is not None:
            metadata_parts["timestamp_ms"].append(timestamps[target_indices])
        metadata_parts["frame_index"].append(
            frame_indices[target_indices] if frame_indices is not None else target_indices
        )
        metadata_parts["annotation_provenance"].append(np.repeat(provenance, count))

    if not windows:
        raise ValueError(
            f"No full valid windows could be built from sequence manifest {manifest_path}"
        )
    total_windows = sum(len(values) for values in windows)
    metadata = {}
    for key, parts in metadata_parts.items():
        if not parts:
            continue
        values = np.concatenate(parts)
        # Optional metadata is useful only when present for every evaluated
        # sample; partial arrays would silently misalign event measurements.
        if len(values) == total_windows:
            metadata[key] = values
    dataset = WindowDataset(
        np.concatenate(windows, axis=0),
        np.concatenate(postures),
        np.concatenate(events),
        metadata,
    )
    # Non-sample attributes are safe for introspection/artifact metadata and
    # do not participate in DataLoader collation.
    dataset.feature_names = expected_feature_names or ()
    return dataset


def _window_from_record(
    row: Mapping[str, Any],
    *,
    index_path: Path,
    cache: dict[Path, dict[str, np.ndarray]],
) -> tuple[np.ndarray, int, int, dict[str, Any]]:
    path_value = _first_value(row, PATH_KEYS)
    data_path = _resolve_record_path(str(path_value), index_path.parent)
    if data_path not in cache:
        with np.load(data_path, allow_pickle=False) as archive:
            cache[data_path] = {key: np.asarray(archive[key]).copy() for key in archive.files}
    arrays = cache[data_path]

    feature_key = next((key for key in FEATURE_KEYS if key in arrays), None)
    if feature_key is not None:
        all_windows = np.asarray(arrays[feature_key])
        sample_index = _optional_int(row, ("sample_index", "window_index", "index"))
        if all_windows.ndim >= 3 and sample_index is not None:
            window = all_windows[sample_index]
            default_target = sample_index
        elif all_windows.ndim >= 3 and all_windows.shape[0] == 1:
            window = all_windows[0]
            default_target = 0
        elif all_windows.ndim == 2:
            window = all_windows
            default_target = 0
        else:
            raise ValueError(
                "Consolidated window NPZ needs sample_index/window_index in its index row"
            )
    elif "landmarks" in arrays:
        landmarks = np.asarray(arrays["landmarks"])
        start_key, start = _required_int_field(row, START_KEYS)
        end_key, end = _required_int_field(row, END_KEYS)
        if start_key == "start_frame":
            start = _frame_number_to_index(start, arrays)
        if end_key == "end_frame":
            end = _frame_number_to_index(end, arrays)
        # ``end_index``/``end_frame`` match CausalWindowBatch.end_indices and
        # identify the included target frame. Explicit ``window_end``/``end``
        # retain conventional Python-exclusive bounds.
        end_exclusive = end + 1 if end_key in {"end_index", "end_frame"} else end
        if start < 0 or end_exclusive <= start or end_exclusive > landmarks.shape[0]:
            raise ValueError(f"Invalid causal window bounds [{start}, {end_exclusive})")
        window = landmarks[start:end_exclusive]
        default_target = end_exclusive - 1
    else:
        raise ValueError(f"{data_path} has neither features/windows nor landmarks")

    explicit_target_field = _optional_int_field(row, TARGET_KEYS)
    if explicit_target_field is None:
        target = default_target
    else:
        target_key, target = explicit_target_field
        if target_key == "target_frame":
            target = _frame_number_to_index(target, arrays)
    posture = _label_from_row_or_arrays(row, POSTURE_KEYS, arrays, target)
    event = _label_from_row_or_arrays(row, EVENT_KEYS, arrays, target)

    ignored_keys = set(PATH_KEYS + POSTURE_KEYS + EVENT_KEYS)
    metadata = {
        str(key): value
        for key, value in row.items()
        if key not in ignored_keys and value not in ("", None)
    }
    metadata["source_path"] = str(data_path.resolve())
    metadata.setdefault("target_index", target)
    if "timestamps_ms" in arrays and 0 <= target < len(arrays["timestamps_ms"]):
        metadata.setdefault("timestamp_ms", int(arrays["timestamps_ms"][target]))
    if "frame_indices" in arrays and 0 <= target < len(arrays["frame_indices"]):
        metadata.setdefault("frame_index", int(arrays["frame_indices"][target]))
    metadata.setdefault("sequence_id", str(row.get("sequence_id", data_path.stem)))
    metadata.setdefault("video_id", str(row.get("video_id", metadata["sequence_id"])))
    return np.asarray(window, dtype=np.float32), posture, event, metadata


def _read_index_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, Mapping):
        payload = payload.get("windows", payload.get("records"))
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        raise ValueError("JSON window index must be a list or contain a windows/records list")
    return [dict(item) for item in payload]


def _metadata_from_archive(
    archive: Any,
    sample_count: int,
) -> dict[str, np.ndarray]:
    ignored = set(FEATURE_KEYS + POSTURE_KEYS + EVENT_KEYS)
    metadata: dict[str, np.ndarray] = {}
    aliases = {
        "sequence_ids": "sequence_id",
        "video_ids": "video_id",
        "group_ids": "group_id",
        "camera_ids": "camera_id",
        "timestamps_ms": "timestamp_ms",
        "frame_indices": "frame_index",
    }
    for key in archive.files:
        if key in ignored:
            continue
        values = np.asarray(archive[key])
        if values.ndim == 0:
            values = np.repeat(values.item(), sample_count)
        elif values.shape[0] != sample_count:
            continue
        elif values.ndim > 1:
            # Per-frame metadata is represented at the causal target (last
            # frame), consistent with the label contract.
            values = values.reshape(sample_count, -1)[:, -1]
        metadata[aliases.get(key, key)] = values
    return metadata


def _targets_from_archive(values: np.ndarray, sample_count: int, name: str) -> np.ndarray:
    target_values = np.asarray(values)
    if target_values.ndim > 1 and target_values.shape[0] == sample_count:
        target_values = target_values.reshape(sample_count, -1)[:, -1]
    if target_values.shape != (sample_count,):
        raise ValueError(
            f"{name} must have one value per window or per timestep, got "
            f"{target_values.shape} for {sample_count} windows"
        )
    return target_values


def _target_values(
    values: np.ndarray | torch.Tensor | Sequence[int | float],
    sample_count: int,
    name: str,
) -> np.ndarray:
    targets = _as_numpy(values)
    if targets.ndim > 1 and targets.shape[0] == sample_count:
        targets = targets.reshape(sample_count, -1)[:, -1]
    if targets.shape != (sample_count,):
        raise ValueError(f"{name} must have shape ({sample_count},), got {targets.shape}")
    if not np.isfinite(targets.astype(np.float64)).all():
        raise ValueError(f"{name} contains NaN or infinite values")
    if not np.all(targets == np.floor(targets)):
        raise ValueError(f"{name} must contain integer labels")
    return targets.astype(np.int64, copy=False)


def _normalize_metadata(
    metadata: Mapping[str, Sequence[Any] | np.ndarray],
    sample_count: int,
) -> dict[str, np.ndarray]:
    normalized: dict[str, np.ndarray] = {}
    for key, values in metadata.items():
        array = _as_numpy(values)
        if array.ndim == 0:
            array = np.repeat(array.item(), sample_count)
        if array.shape != (sample_count,):
            raise ValueError(
                f"metadata[{key!r}] must have shape ({sample_count},), got {array.shape}"
            )
        normalized[str(key)] = array
    return normalized


def _transpose_metadata(rows: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    common_keys = set(rows[0])
    for row in rows[1:]:
        common_keys.intersection_update(row)
    return {key: np.asarray([row[key] for row in rows]) for key in sorted(common_keys)}


def _label_from_row_or_arrays(
    row: Mapping[str, Any],
    aliases: Sequence[str],
    arrays: Mapping[str, np.ndarray],
    target: int,
) -> int:
    for key in aliases:
        value = row.get(key)
        if value not in (None, ""):
            return int(value)
    for key in aliases:
        if key in arrays:
            labels = np.asarray(arrays[key]).reshape(-1)
            if target < 0 or target >= len(labels):
                raise ValueError(f"Target index {target} outside {key} labels")
            return int(labels[target])
    raise KeyError(f"Index row/source is missing label field from {tuple(aliases)}")


def _frame_number_to_index(target: int, arrays: Mapping[str, np.ndarray]) -> int:
    if "frame_indices" not in arrays:
        return target
    matches = np.flatnonzero(np.asarray(arrays["frame_indices"]).reshape(-1) == target)
    return int(matches[0]) if matches.size else target


def _first_key(keys: set[str], aliases: Sequence[str], description: str) -> str:
    key = next((candidate for candidate in aliases if candidate in keys), None)
    if key is None:
        raise ValueError(f"NPZ is missing {description}; accepted keys: {tuple(aliases)}")
    return key


def _first_value(mapping: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for key in aliases:
        if key in mapping:
            return mapping[key]
    raise KeyError(f"Missing required field; accepted keys: {tuple(aliases)}")


def _resolve_split_file(directory: Path, split: str | None) -> Path:
    if split is None:
        npz_files = sorted(directory.glob("*.npz"))
        index_files = sorted(
            path for suffix in ("*.csv", "*.json", "*.jsonl") for path in directory.glob(suffix)
        )
        candidates = npz_files + index_files
        if len(candidates) == 1:
            return candidates[0]
        raise ValueError(
            f"split is required for dataset directory {directory} "
            f"(found {len(candidates)} candidate files)"
        )
    normalized = _normalize_split(split)
    names = [normalized]
    if normalized == "validation":
        names.append("val")
    candidates: list[Path] = []
    for name in names:
        candidates.extend(
            [
                directory / f"windows_{name}.npz",
                directory / f"{name}.npz",
                directory / f"windows_{name}.csv",
                directory / f"{name}.csv",
                directory / f"windows_{name}.jsonl",
                directory / name / "windows.npz",
                directory / "sequences.csv",
            ]
        )
    found = next((candidate for candidate in candidates if candidate.is_file()), None)
    if found is None:
        raise FileNotFoundError(f"No processed dataset found for split={split!r} in {directory}")
    return found


def _resolve_record_path(value: str, base_directory: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        resolved = path
    elif path.is_file():
        resolved = path.resolve()
    else:
        resolved = (base_directory / path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Indexed NPZ file not found: {resolved}")
    return resolved


def _normalize_split(split: str) -> str:
    normalized = split.strip().lower()
    return "validation" if normalized in {"val", "valid", "dev"} else normalized


def _required_int_field(
    row: Mapping[str, Any],
    aliases: Sequence[str],
) -> tuple[str, int]:
    result = _optional_int_field(row, aliases)
    if result is not None:
        return result
    raise KeyError(f"Missing required integer field from {tuple(aliases)}")


def _optional_int_field(
    row: Mapping[str, Any],
    aliases: Sequence[str],
) -> tuple[str, int] | None:
    for key in aliases:
        value = row.get(key)
        if value not in (None, ""):
            return key, int(value)
    return None


def _optional_int(row: Mapping[str, Any], aliases: Sequence[str]) -> int | None:
    for key in aliases:
        value = row.get(key)
        if value not in (None, ""):
            return int(value)
    return None


def _npz_scalar_to_string(value: np.ndarray) -> str:
    array = np.asarray(value)
    if array.size != 1:
        raise ValueError("annotation_provenance must be a scalar")
    scalar = array.reshape(-1)[0]
    if isinstance(scalar, bytes):
        return scalar.decode("utf-8")
    return str(scalar)


def _feature_value(config: Any, name: str, default: Any) -> Any:
    if config is None:
        return default
    if hasattr(config, "features"):
        config = config.features
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _as_numpy(value: Any, *, dtype: Any = None) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()
    return np.asarray(value, dtype=dtype)


def _collatable_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _seed_worker(worker_id: int) -> None:
    del worker_id
    worker_seed = torch.initial_seed() % (2**32)
    np.random.seed(worker_seed)
