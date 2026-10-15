from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, Dataset, Subset

from fallguard.models.predictor import TemporalPredictor, unpack_temporal_output
from fallguard.training.data import WindowDataset, build_dataloader, load_window_dataset
from fallguard.training.evaluate import resolve_device, unpack_batch, write_metrics
from fallguard.training.metrics import binary_event_metrics, event_level_metrics

CalibrationObjective = Literal["frame_f1", "event_f1"]
CalibrationMethod = Literal["grid", "pr_curve"]


def calibrate_event_threshold(
    checkpoint_or_model: str | Path | nn.Module | TemporalPredictor,
    validation_data: (
        str | Path | WindowDataset | Dataset[Any] | DataLoader[Any] | Mapping[str, Any]
    ),
    *,
    objective: CalibrationObjective = "event_f1",
    method: CalibrationMethod = "grid",
    thresholds: Sequence[float] | np.ndarray | None = None,
    grid_size: int = 101,
    batch_size: int = 64,
    num_workers: int = 0,
    device: str | torch.device = "auto",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Choose an event threshold using validation labels and write provenance.

    This API intentionally has no test-data argument. Sources whose path or
    sample metadata identifies a test split are rejected. The resulting
    ``calibration.json`` can then be passed unchanged to final test evaluation.
    """

    normalized_objective = _normalize_objective(objective)
    normalized_method = _normalize_method(method)
    _assert_not_test_source(validation_data)
    resolved_device = resolve_device(device)
    model, checkpoint_path = _resolve_model(checkpoint_or_model, resolved_device)
    loader, source_description = _validation_loader(
        validation_data,
        batch_size=batch_size,
        num_workers=num_workers,
        device=resolved_device,
    )
    labels, probabilities, metadata = _collect_event_outputs(
        model,
        loader,
        device=resolved_device,
    )
    _assert_validation_metadata(metadata)
    if not np.any(labels == 1) or not np.any(labels == 0):
        raise ValueError(
            "Threshold calibration requires both positive and negative event "
            "labels in the validation split"
        )
    if normalized_objective == "event_f1" and "sequence_id" not in metadata:
        raise ValueError(
            "objective='event_f1' requires sequence_id metadata; use "
            "objective='frame_f1' only when event grouping is unavailable"
        )

    candidates = _candidate_thresholds(
        probabilities,
        method=normalized_method,
        thresholds=thresholds,
        grid_size=grid_size,
    )
    curve = [
        _score_threshold(
            labels,
            probabilities,
            threshold=float(threshold),
            metadata=metadata,
        )
        for threshold in candidates
    ]
    objective_key = "event" if normalized_objective == "event_f1" else "frame"
    chosen = max(
        curve,
        key=lambda row: (
            row[objective_key]["f1"],
            row[objective_key]["recall"],
            row[objective_key]["precision"],
            -row["threshold"],
        ),
    )
    chosen_threshold = float(chosen["threshold"])
    chosen_predictions = (probabilities >= chosen_threshold).astype(np.int64)
    frame_report = binary_event_metrics(labels, chosen_predictions)
    event_report = (
        event_level_metrics(
            labels,
            chosen_predictions,
            sequence_ids=metadata["sequence_id"],
            video_ids=metadata.get("video_id"),
            timestamps_ms=metadata.get("timestamp_ms"),
            frame_indices=metadata.get("frame_index"),
        )
        if "sequence_id" in metadata
        else {
            "available": False,
            "reason": "sequence_id metadata was not provided",
        }
    )

    destination = _calibration_path(output_path, checkpoint_path)
    result: dict[str, Any] = {
        "calibration_schema_version": 1,
        "chosen_threshold": chosen_threshold,
        "objective": normalized_objective,
        "objective_score": float(chosen[objective_key]["f1"]),
        "method": normalized_method,
        "tie_break_policy": "f1, then recall, then precision, then lower threshold",
        "chosen_metrics": {
            "frame": frame_report,
            "event": event_report,
        },
        "candidate_count": len(curve),
        "threshold_curve": curve,
        "provenance": {
            "created_at": datetime.now(UTC).isoformat(),
            "data_role": "validation_only",
            "test_data_accessed": False,
            "validation_source": source_description,
            "validation_samples": int(labels.size),
            "validation_positive_frames": int(labels.sum()),
            "validation_negative_frames": int((labels == 0).sum()),
            "validation_sequences": (
                int(len(np.unique(metadata["sequence_id"]))) if "sequence_id" in metadata else None
            ),
            "checkpoint": (
                _file_provenance(checkpoint_path) if checkpoint_path is not None else None
            ),
            "model_class": type(model).__name__,
        },
    }
    write_metrics(result, destination)
    result["calibration_path"] = str(destination)
    return result


def load_calibrated_threshold(path: str | Path) -> float:
    """Read and validate only the selected threshold from calibration JSON."""

    import json

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Calibration file not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping) or "chosen_threshold" not in payload:
        raise ValueError("Calibration file does not contain chosen_threshold")
    threshold = float(payload["chosen_threshold"])
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("Calibrated threshold must be in [0, 1]")
    return threshold


def _resolve_model(
    checkpoint_or_model: str | Path | nn.Module | TemporalPredictor,
    device: torch.device,
) -> tuple[nn.Module, Path | None]:
    if isinstance(checkpoint_or_model, TemporalPredictor):
        model = checkpoint_or_model.model.to(device)
        model.eval()
        return model, checkpoint_or_model.checkpoint_path
    if isinstance(checkpoint_or_model, (str, Path)):
        checkpoint_path = Path(checkpoint_or_model)
        predictor = TemporalPredictor.from_checkpoint(checkpoint_path, device=device)
        return predictor.model, checkpoint_path
    if not isinstance(checkpoint_or_model, nn.Module):
        raise TypeError("checkpoint_or_model must be a checkpoint path or torch module")
    model = checkpoint_or_model.to(device)
    model.eval()
    return model, None


def _validation_loader(
    validation_data: Any,
    *,
    batch_size: int,
    num_workers: int,
    device: torch.device,
) -> tuple[DataLoader[Any], dict[str, Any]]:
    if isinstance(validation_data, DataLoader):
        return validation_data, {
            "kind": "dataloader",
            "dataset_class": type(validation_data.dataset).__name__,
            "split_verification": "sample metadata or caller validation_data contract",
        }

    source_path: Path | None = None
    data = validation_data
    if isinstance(data, (str, Path)):
        source_path = Path(data)
        data = load_window_dataset(data, split="validation")
    elif isinstance(data, Mapping):
        data = load_window_dataset(data)
    if not isinstance(data, Dataset):
        raise TypeError("validation_data must be a dataset, dataloader, path, or array mapping")
    loader = build_dataloader(
        data,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        seed=0,
        device=device,
    )
    source = {
        "kind": "path" if source_path is not None else "in_memory_dataset",
        "dataset_class": type(data).__name__,
        "split_verification": (
            "loader selected split=validation"
            if source_path is not None
            else "sample metadata or caller validation_data contract"
        ),
    }
    if source_path is not None:
        source["path"] = str(source_path.resolve())
        if source_path.is_file():
            source["sha256"] = _sha256(source_path)
    return loader, source


def _collect_event_outputs(
    model: nn.Module,
    loader: DataLoader[Any],
    *,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    labels: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    metadata_parts: dict[str, list[np.ndarray]] = {
        "sequence_id": [],
        "video_id": [],
        "timestamp_ms": [],
        "frame_index": [],
        "split": [],
    }
    sample_count = 0
    with torch.inference_mode():
        for batch in loader:
            features, _, event_labels = unpack_batch(batch)
            features = features.to(device, non_blocking=True)
            output = model(features)
            _, event_logits = unpack_temporal_output(output)
            batch_probabilities = torch.sigmoid(event_logits.reshape(-1))
            labels.append(event_labels.detach().cpu().numpy().reshape(-1))
            probabilities.append(batch_probabilities.detach().cpu().numpy().reshape(-1))
            batch_count = int(features.shape[0])
            sample_count += batch_count
            if isinstance(batch, Mapping):
                for key in metadata_parts:
                    if key in batch:
                        metadata_parts[key].append(_to_numpy(batch[key]))
    if not sample_count:
        raise ValueError("Cannot calibrate on an empty validation dataset")

    label_values = np.concatenate(labels).astype(np.int64)
    probability_values = np.concatenate(probabilities).astype(np.float64)
    if not np.all(np.isin(label_values, (0, 1))):
        raise ValueError("Validation event labels must contain only 0 and 1")
    if not np.isfinite(probability_values).all():
        raise ValueError("Model produced non-finite validation probabilities")
    metadata = {
        key: np.concatenate(parts)
        for key, parts in metadata_parts.items()
        if parts and sum(len(part) for part in parts) == sample_count
    }
    return label_values, probability_values, metadata


def _candidate_thresholds(
    probabilities: np.ndarray,
    *,
    method: CalibrationMethod,
    thresholds: Sequence[float] | np.ndarray | None,
    grid_size: int,
) -> np.ndarray:
    if thresholds is not None:
        candidates = np.asarray(thresholds, dtype=np.float64).reshape(-1)
        if not len(candidates):
            raise ValueError("thresholds must not be empty")
    elif method == "grid":
        if grid_size < 2:
            raise ValueError("grid_size must be at least 2")
        candidates = np.linspace(0.0, 1.0, grid_size, dtype=np.float64)
    else:
        # Every distinct prediction boundary is an exact binary PR operating
        # point under the project's >= threshold decision rule.
        candidates = np.concatenate(
            (
                np.asarray([0.0, 1.0], dtype=np.float64),
                np.unique(probabilities),
            )
        )
    if not np.isfinite(candidates).all() or np.any((candidates < 0.0) | (candidates > 1.0)):
        raise ValueError("All calibration thresholds must be finite values in [0, 1]")
    return np.unique(candidates)


def _score_threshold(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    threshold: float,
    metadata: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(np.int64)
    frame = binary_event_metrics(labels, predictions)
    row: dict[str, Any] = {
        "threshold": float(threshold),
        "frame": {
            "precision": float(frame["precision"]),
            "recall": float(frame["recall"]),
            "f1": float(frame["f1"]),
            "predicted_positive_frames": int(predictions.sum()),
        },
    }
    if "sequence_id" in metadata:
        event = event_level_metrics(
            labels,
            predictions,
            sequence_ids=metadata["sequence_id"],
            video_ids=metadata.get("video_id"),
            timestamps_ms=metadata.get("timestamp_ms"),
            frame_indices=metadata.get("frame_index"),
        )
        row["event"] = {
            "precision": float(event["precision"]),
            "recall": float(event["recall"]),
            "f1": float(event["f1"]),
            "true_positives": int(event["true_positives"]),
            "false_positives": int(event["false_positives"]),
            "false_negatives": int(event["false_negatives"]),
        }
    return row


def _assert_not_test_source(validation_data: Any) -> None:
    if isinstance(validation_data, DataLoader):
        _assert_not_test_source(validation_data.dataset)
        return
    if isinstance(validation_data, Subset):
        dataset = validation_data.dataset
        metadata = getattr(dataset, "metadata", None)
        if isinstance(metadata, Mapping) and "split" in metadata:
            selected = np.asarray(metadata["split"])[
                np.asarray(validation_data.indices, dtype=np.int64)
            ]
            _assert_validation_metadata({"split": selected})
        else:
            _assert_not_test_source(dataset)
        return
    if isinstance(validation_data, WindowDataset):
        _assert_validation_metadata(validation_data.metadata)
        return
    if isinstance(validation_data, Mapping):
        nested_metadata = validation_data.get("metadata")
        split_values = validation_data.get("split", validation_data.get("splits"))
        if split_values is None and isinstance(nested_metadata, Mapping):
            split_values = nested_metadata.get("split", nested_metadata.get("splits"))
        if split_values is not None:
            _assert_validation_metadata({"split": np.asarray(split_values).reshape(-1)})
        return
    metadata = getattr(validation_data, "metadata", None)
    if isinstance(metadata, Mapping):
        _assert_validation_metadata(metadata)
        return
    if not isinstance(validation_data, (str, Path)):
        return
    path = Path(validation_data)
    if any("test" in re.split(r"[_.-]+", part.casefold()) for part in path.parts):
        raise ValueError(
            f"Calibration is validation-only and refuses a test-labelled source: {path}"
        )


def _assert_validation_metadata(metadata: Mapping[str, np.ndarray]) -> None:
    if "split" not in metadata:
        return
    observed = {
        (
            "validation"
            if str(value).strip().lower() in {"val", "valid", "dev"}
            else str(value).strip().lower()
        )
        for value in metadata["split"]
    }
    if observed != {"validation"}:
        raise ValueError(
            f"Calibration is validation-only; sample metadata contains split(s): {sorted(observed)}"
        )


def _normalize_objective(value: str) -> CalibrationObjective:
    normalized = value.strip().lower()
    aliases = {
        "frame": "frame_f1",
        "frame_f1": "frame_f1",
        "event": "event_f1",
        "event_f1": "event_f1",
    }
    try:
        return aliases[normalized]  # type: ignore[return-value]
    except KeyError as error:
        raise ValueError("objective must be 'frame_f1' or 'event_f1'") from error


def _normalize_method(value: str) -> CalibrationMethod:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in {"grid", "pr_curve"}:
        raise ValueError("method must be 'grid' or 'pr_curve'")
    return normalized  # type: ignore[return-value]


def _calibration_path(
    output_path: str | Path | None,
    checkpoint_path: Path | None,
) -> Path:
    if output_path is not None:
        path = Path(output_path)
        if path.exists() and path.is_dir():
            return path / "calibration.json"
        if not path.suffix:
            return path / "calibration.json"
        return path
    if checkpoint_path is not None:
        return checkpoint_path.parent / "calibration.json"
    return Path("calibration.json")


def _file_provenance(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": _sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _to_numpy(values: Any) -> np.ndarray:
    if isinstance(values, Tensor):
        return values.detach().cpu().numpy().reshape(-1)
    return np.asarray(values).reshape(-1)
