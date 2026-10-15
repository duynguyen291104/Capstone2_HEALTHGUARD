from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, Dataset

from fallguard.models.predictor import TemporalPredictor, unpack_temporal_output
from fallguard.training.data import WindowDataset, build_dataloader, load_window_dataset
from fallguard.training.losses import MultiTaskLoss
from fallguard.training.metrics import evaluate_predictions


def evaluate_model(
    model: nn.Module,
    data: WindowDataset | Dataset[Any] | DataLoader[Any] | str | Path | Mapping[str, Any],
    *,
    batch_size: int = 64,
    num_workers: int = 0,
    device: str | torch.device = "auto",
    event_threshold: float = 0.5,
    criterion: MultiTaskLoss | None = None,
    output_path: str | Path | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    """Evaluate both heads and event regions without inventing unavailable values."""

    resolved_device = resolve_device(device)
    loader = _as_evaluation_loader(
        data,
        batch_size=batch_size,
        num_workers=num_workers,
        device=resolved_device,
        split=split,
    )
    model = model.to(resolved_device)
    model.eval()
    if criterion is not None:
        criterion = criterion.to(resolved_device)

    posture_truth: list[np.ndarray] = []
    posture_predictions: list[np.ndarray] = []
    event_truth: list[np.ndarray] = []
    event_probabilities: list[np.ndarray] = []
    metadata: dict[str, list[np.ndarray]] = {
        "sequence_id": [],
        "video_id": [],
        "timestamp_ms": [],
        "frame_index": [],
    }
    loss_sums = {"total": 0.0, "posture": 0.0, "event": 0.0}
    evaluated_samples = 0

    with torch.inference_mode():
        for batch in loader:
            features, posture_labels, event_labels = unpack_batch(batch)
            features = features.to(resolved_device, non_blocking=True)
            posture_labels = posture_labels.to(resolved_device, non_blocking=True)
            event_labels = event_labels.to(resolved_device, non_blocking=True)
            output = model(features)
            posture_logits, event_logits = unpack_temporal_output(output)
            probabilities = torch.sigmoid(event_logits.reshape(-1))
            predicted_posture = posture_logits.argmax(dim=-1)
            batch_size_actual = int(features.shape[0])

            posture_truth.append(posture_labels.detach().cpu().numpy())
            posture_predictions.append(predicted_posture.detach().cpu().numpy())
            event_truth.append(event_labels.detach().cpu().numpy())
            event_probabilities.append(probabilities.detach().cpu().numpy())
            evaluated_samples += batch_size_actual

            if criterion is not None:
                losses = criterion(output, posture_labels, event_labels)
                loss_sums["total"] += float(losses.total) * batch_size_actual
                loss_sums["posture"] += float(losses.posture) * batch_size_actual
                loss_sums["event"] += float(losses.event) * batch_size_actual
            if isinstance(batch, Mapping):
                for key in metadata:
                    if key in batch:
                        metadata[key].append(_batch_to_numpy(batch[key]))

    if not evaluated_samples:
        raise ValueError("Cannot evaluate an empty dataset")
    posture_true_array = np.concatenate(posture_truth).astype(np.int64)
    posture_pred_array = np.concatenate(posture_predictions).astype(np.int64)
    event_true_array = np.concatenate(event_truth).astype(np.int64)
    event_probability_array = np.concatenate(event_probabilities).astype(np.float64)
    metadata_arrays = {
        key: np.concatenate(parts)
        for key, parts in metadata.items()
        if parts and sum(len(part) for part in parts) == evaluated_samples
    }

    posture_classes = int(getattr(model, "posture_classes", 3))
    default_names = ("UPRIGHT", "TRANSITION", "LYING")
    class_names = (
        default_names
        if posture_classes == len(default_names)
        else tuple(f"CLASS_{index}" for index in range(posture_classes))
    )
    metrics = evaluate_predictions(
        posture_true_array,
        posture_pred_array,
        event_true_array,
        event_probability_array,
        event_threshold=event_threshold,
        sequence_ids=metadata_arrays.get("sequence_id"),
        video_ids=metadata_arrays.get("video_id"),
        timestamps_ms=metadata_arrays.get("timestamp_ms"),
        frame_indices=metadata_arrays.get("frame_index"),
        posture_class_names=class_names,
    )
    metrics["sample_count"] = evaluated_samples
    if criterion is not None:
        metrics["loss"] = {key: value / evaluated_samples for key, value in loss_sums.items()}
    if output_path is not None:
        write_metrics(metrics, output_path)
    return metrics


def evaluate_checkpoint(
    checkpoint_path: str | Path,
    data: WindowDataset | Dataset[Any] | DataLoader[Any] | str | Path | Mapping[str, Any],
    *,
    device: str | torch.device = "auto",
    model_config: Any = None,
    input_size: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if input_size is None and isinstance(data, WindowDataset):
        input_size = data.input_size
    predictor = TemporalPredictor.from_checkpoint(
        checkpoint_path,
        device=device,
        model_config=model_config,
        input_size=input_size,
    )
    return evaluate_model(
        predictor.model,
        data,
        device=predictor.device,
        **kwargs,
    )


evaluate = evaluate_model


def unpack_batch(batch: Any) -> tuple[Tensor, Tensor, Tensor]:
    if isinstance(batch, Mapping):
        features = _mapping_tensor(batch, ("features", "windows", "x", "X"))
        posture = _mapping_tensor(
            batch,
            ("posture_label", "posture_labels", "posture", "y_posture"),
        )
        event = _mapping_tensor(
            batch,
            ("event_label", "event_labels", "event", "y_event"),
        )
    elif isinstance(batch, (tuple, list)) and len(batch) >= 3:
        features, posture, event = batch[:3]
    else:
        raise TypeError("Batch must be a mapping or (features, posture, event) tuple")
    features = torch.as_tensor(features, dtype=torch.float32)
    if features.ndim < 3:
        raise ValueError("Batch features must have shape [batch, time, ...features]")
    features = features.flatten(start_dim=2)
    posture = torch.as_tensor(posture, dtype=torch.long).reshape(-1)
    event = torch.as_tensor(event, dtype=torch.float32).reshape(-1)
    if features.shape[0] != posture.shape[0] or features.shape[0] != event.shape[0]:
        raise ValueError("Batch features and labels have different sample counts")
    return features, posture, event


def resolve_device(device: str | torch.device) -> torch.device:
    if isinstance(device, torch.device):
        requested = device
    else:
        normalized = str(device).strip().lower()
        if normalized == "auto":
            normalized = "cuda" if torch.cuda.is_available() else "cpu"
        if normalized not in {"cpu", "cuda"} and not normalized.startswith("cuda:"):
            raise ValueError(f"Unsupported device: {device}")
        requested = torch.device(normalized)
    if requested.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return requested


def write_metrics(metrics: Mapping[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _as_evaluation_loader(
    data: Any,
    *,
    batch_size: int,
    num_workers: int,
    device: torch.device,
    split: str | None,
) -> DataLoader[Any]:
    if isinstance(data, DataLoader):
        return data
    if isinstance(data, (str, Path, Mapping)):
        data = load_window_dataset(data, split=split)
    if not isinstance(data, Dataset):
        raise TypeError("data must be a Dataset, DataLoader, path, or array mapping")
    return build_dataloader(
        data,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        device=device,
    )


def _mapping_tensor(batch: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in batch:
            return batch[key]
    raise KeyError(f"Batch is missing one of {aliases}")


def _batch_to_numpy(values: Any) -> np.ndarray:
    if isinstance(values, Tensor):
        return values.detach().cpu().numpy().reshape(-1)
    if isinstance(values, (list, tuple)):
        return np.asarray(values).reshape(-1)
    return np.asarray(values).reshape(-1)
