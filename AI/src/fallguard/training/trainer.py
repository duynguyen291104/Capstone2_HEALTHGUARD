from __future__ import annotations

import csv
import json
import os
import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset

from fallguard.models.predictor import unpack_temporal_output
from fallguard.models.temporal import MultiTaskTemporalModel, build_model
from fallguard.training.data import WindowDataset, build_dataloader, load_window_dataset
from fallguard.training.evaluate import evaluate_model, resolve_device, unpack_batch, write_metrics
from fallguard.training.losses import (
    MultiTaskLoss,
    balanced_class_weights,
    event_positive_weight,
)


@dataclass(slots=True)
class TrainingResult:
    artifact_dir: Path
    best_checkpoint: Path
    last_checkpoint: Path
    metrics_path: Path
    metadata_path: Path
    history_path: Path
    best_epoch: int
    epochs_completed: int
    early_stopped: bool
    metrics: dict[str, Any]
    model: nn.Module

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "artifact_dir": self.artifact_dir,
                "best_checkpoint": self.best_checkpoint,
                "last_checkpoint": self.last_checkpoint,
                "metrics_path": self.metrics_path,
                "metadata_path": self.metadata_path,
                "history_path": self.history_path,
                "best_epoch": self.best_epoch,
                "epochs_completed": self.epochs_completed,
                "early_stopped": self.early_stopped,
                "metrics": self.metrics,
            }
        )


class Trainer:
    """Deterministic trainer that owns the complete artifact contract."""

    def __init__(
        self,
        config: Any,
        *,
        output_dir: str | Path | None = None,
        model: nn.Module | None = None,
        event_threshold: float = 0.5,
    ) -> None:
        self.config = config
        self.output_dir = (
            Path(output_dir)
            if output_dir is not None
            else Path(_config_value(config, "paths.artifact_dir", "artifacts"))
        )
        self.model = model
        self.event_threshold = float(event_threshold)

    def fit(
        self,
        train_data: Any = None,
        validation_data: Any = None,
    ) -> TrainingResult:
        return _fit(
            self.config,
            train_data=train_data,
            validation_data=validation_data,
            output_dir=self.output_dir,
            model=self.model,
            event_threshold=self.event_threshold,
        )


def train_model(
    config: Any,
    train_data: Any = None,
    validation_data: Any = None,
    output_dir: str | Path | None = None,
    *,
    model: nn.Module | None = None,
    event_threshold: float = 0.5,
) -> TrainingResult:
    return Trainer(
        config,
        output_dir=output_dir,
        model=model,
        event_threshold=event_threshold,
    ).fit(train_data, validation_data)


train = train_model


def _fit(
    config: Any,
    *,
    train_data: Any,
    validation_data: Any,
    output_dir: Path,
    model: nn.Module | None,
    event_threshold: float,
) -> TrainingResult:
    seed = int(_config_value(config, "project.seed", 42))
    set_deterministic(seed)
    device = resolve_device(_config_value(config, "project.device", "auto"))
    processed_dir = Path(_config_value(config, "paths.processed_dir", "data/processed"))
    window_size = int(_config_value(config, "data.window_size", 40))
    stride = int(_config_value(config, "data.stride", 5))
    max_interpolation_gap = int(_config_value(config, "data.max_interpolation_gap", 3))
    feature_config = _nested_config(config, "features")
    training_source = train_data if train_data is not None else processed_dir
    train_dataset = _coerce_training_dataset(
        training_source,
        split="train",
        window_size=window_size,
        stride=stride,
        feature_config=feature_config,
        max_interpolation_gap=max_interpolation_gap,
    )
    validation_source = validation_data if validation_data is not None else training_source
    validation_dataset = _coerce_training_dataset(
        validation_source,
        split="validation",
        window_size=window_size,
        stride=stride,
        feature_config=feature_config,
        max_interpolation_gap=max_interpolation_gap,
    )
    monitor_split = (
        "training"
        if validation_data is None and validation_dataset is train_dataset
        else "validation"
    )
    if train_dataset.input_size != validation_dataset.input_size:
        raise ValueError(
            "Train and validation feature sizes differ: "
            f"{train_dataset.input_size} != {validation_dataset.input_size}"
        )

    posture_classes = int(_config_value(config, "model.posture_classes", 3))
    for split_name, dataset in (
        ("train", train_dataset),
        ("validation", validation_dataset),
    ):
        if int(dataset.posture_labels.max()) >= posture_classes:
            raise ValueError(
                f"{split_name} has posture label {int(dataset.posture_labels.max())}, "
                f"but model.posture_classes={posture_classes}"
            )

    if model is None:
        model = build_model(
            _nested_config(config, "model"),
            input_size=train_dataset.input_size,
        )
    model_input_size = getattr(model, "input_size", train_dataset.input_size)
    if int(model_input_size) != train_dataset.input_size:
        raise ValueError(
            f"Model expects {model_input_size} features; dataset has {train_dataset.input_size}"
        )
    model = model.to(device)

    batch_size = int(_config_value(config, "training.batch_size", 32))
    num_workers = int(_config_value(config, "training.num_workers", 0))
    train_loader = build_dataloader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        seed=seed,
        device=device,
    )
    validation_loader = build_dataloader(
        validation_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        seed=seed,
        device=device,
    )

    use_weighted_loss = bool(_config_value(config, "training.weighted_loss", True))
    posture_weights = (
        balanced_class_weights(
            train_dataset.posture_labels,
            num_classes=posture_classes,
        )
        if use_weighted_loss
        else None
    )
    positive_weight = (
        event_positive_weight(train_dataset.event_labels) if use_weighted_loss else None
    )
    criterion = MultiTaskLoss(
        posture_loss_weight=float(_config_value(config, "model.posture_loss_weight", 0.30)),
        posture_class_weights=posture_weights,
        event_pos_weight=positive_weight,
    ).to(device)
    optimizer = AdamW(
        model.parameters(),
        lr=float(_config_value(config, "training.learning_rate", 0.001)),
        weight_decay=float(_config_value(config, "training.weight_decay", 0.0001)),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    best_path = output_dir / "best.pt"
    last_path = output_dir / "last.pt"
    config_path = output_dir / "config.yaml"
    metadata_path = output_dir / "metadata.json"
    history_path = output_dir / "history.csv"
    metrics_path = output_dir / "metrics.json"
    serialized_config = _serialize_config(config)
    _write_yaml(serialized_config, config_path)

    epochs = int(_config_value(config, "training.epochs", 30))
    patience = int(_config_value(config, "training.patience", 7))
    history: list[dict[str, Any]] = []
    best_validation_loss = float("inf")
    best_epoch = 0
    epochs_without_improvement = 0
    early_stopped = False
    architecture = str(getattr(model, "architecture", type(model).__name__.lower()))
    created_at = datetime.now(UTC)
    model_version = f"{architecture}-{created_at.strftime('%Y%m%dT%H%M%SZ')}"
    model_spec = _model_spec(model, config, train_dataset.input_size)

    for epoch in range(1, epochs + 1):
        train_stats = run_epoch(
            model,
            train_loader,
            criterion,
            device=device,
            optimizer=optimizer,
        )
        validation_stats = run_epoch(
            model,
            validation_loader,
            criterion,
            device=device,
            optimizer=None,
        )
        record = {
            "epoch": epoch,
            **{f"train_{key}": value for key, value in train_stats.items()},
            **{f"validation_{key}": value for key, value in validation_stats.items()},
        }
        history.append(record)
        current_loss = validation_stats["total_loss"]
        if not np.isfinite(current_loss):
            raise FloatingPointError(
                f"Validation loss became non-finite at epoch {epoch}: {current_loss}"
            )
        improved = current_loss < best_validation_loss - 1e-12
        if improved:
            best_validation_loss = current_loss
            best_epoch = epoch
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        checkpoint = _checkpoint_payload(
            model=model,
            optimizer=optimizer,
            epoch=epoch,
            best_epoch=best_epoch,
            best_validation_loss=best_validation_loss,
            model_spec=model_spec,
            config=serialized_config,
            model_version=model_version,
            posture_weights=posture_weights,
            positive_weight=positive_weight,
            history=history,
        )
        _atomic_torch_save(checkpoint, last_path)
        if improved:
            _atomic_torch_save(checkpoint, best_path)
        _write_history(history, history_path)

        if epochs_without_improvement >= patience:
            early_stopped = True
            break

    best_checkpoint = _torch_load(best_path)
    model.load_state_dict(best_checkpoint["model_state_dict"])
    metrics = evaluate_model(
        model,
        validation_loader,
        device=device,
        event_threshold=event_threshold,
        criterion=criterion,
    )
    metrics["evaluated_split"] = monitor_split
    metrics["checkpoint"] = "best.pt"
    write_metrics(metrics, metrics_path)

    epochs_completed = len(history)
    metadata = {
        "artifact_schema_version": 1,
        "created_at": created_at.isoformat(),
        "model_version": model_version,
        "architecture": architecture,
        "model_spec": model_spec,
        "parameter_count": int(sum(parameter.numel() for parameter in model.parameters())),
        "trainable_parameter_count": int(
            sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
        ),
        "device_used": str(device),
        "seed": seed,
        "deterministic": True,
        "weighted_loss": use_weighted_loss,
        "posture_class_weights": (
            [float(value) for value in posture_weights.tolist()]
            if posture_weights is not None
            else None
        ),
        "event_positive_weight": (
            float(positive_weight.item()) if positive_weight is not None else None
        ),
        "posture_loss_weight": criterion.posture_loss_weight,
        "event_threshold": event_threshold,
        "best_epoch": best_epoch,
        "best_validation_loss": best_validation_loss,
        "epochs_completed": epochs_completed,
        "early_stopped": early_stopped,
        "training_samples": len(train_dataset),
        "validation_samples": len(validation_dataset),
        "monitor_split": monitor_split,
        "window_size": train_dataset.window_size,
        "input_size": train_dataset.input_size,
        "posture_class_counts": _counts(
            train_dataset.posture_labels,
            posture_classes,
        ),
        "event_class_counts": _counts(train_dataset.event_labels.long(), 2),
        "artifacts": {
            "best_checkpoint": best_path.name,
            "last_checkpoint": last_path.name,
            "config": config_path.name,
            "history": history_path.name,
            "metrics": metrics_path.name,
        },
    }
    _write_json(metadata, metadata_path)
    return TrainingResult(
        artifact_dir=output_dir,
        best_checkpoint=best_path,
        last_checkpoint=last_path,
        metrics_path=metrics_path,
        metadata_path=metadata_path,
        history_path=history_path,
        best_epoch=best_epoch,
        epochs_completed=epochs_completed,
        early_stopped=early_stopped,
        metrics=metrics,
        model=model,
    )


def run_epoch(
    model: nn.Module,
    loader: DataLoader[Any],
    criterion: MultiTaskLoss,
    *,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    totals = {
        "total_loss": 0.0,
        "posture_loss": 0.0,
        "event_loss": 0.0,
        "posture_correct": 0.0,
        "event_correct": 0.0,
    }
    sample_count = 0
    context = torch.enable_grad() if training else torch.inference_mode()
    with context:
        for batch in loader:
            features, posture_labels, event_labels = unpack_batch(batch)
            features = features.to(device, non_blocking=True)
            posture_labels = posture_labels.to(device, non_blocking=True)
            event_labels = event_labels.to(device, non_blocking=True)
            if training:
                optimizer.zero_grad(set_to_none=True)
            output = model(features)
            losses = criterion(output, posture_labels, event_labels)
            if training:
                losses.total.backward()
                optimizer.step()

            posture_logits, event_logits = unpack_temporal_output(output)
            current_batch_size = int(features.shape[0])
            totals["total_loss"] += float(losses.total.detach()) * current_batch_size
            totals["posture_loss"] += float(losses.posture.detach()) * current_batch_size
            totals["event_loss"] += float(losses.event.detach()) * current_batch_size
            totals["posture_correct"] += float(
                (posture_logits.argmax(dim=-1) == posture_labels).sum()
            )
            totals["event_correct"] += float(
                ((event_logits >= 0).long() == event_labels.long()).sum()
            )
            sample_count += current_batch_size
    if not sample_count:
        raise ValueError("Cannot train or validate on an empty dataset")
    return {
        "total_loss": totals["total_loss"] / sample_count,
        "posture_loss": totals["posture_loss"] / sample_count,
        "event_loss": totals["event_loss"] / sample_count,
        "posture_accuracy": totals["posture_correct"] / sample_count,
        "event_accuracy": totals["event_correct"] / sample_count,
    }


def set_deterministic(seed: int) -> None:
    if seed < 0:
        raise ValueError("seed must be non-negative")
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


seed_everything = set_deterministic


def _coerce_training_dataset(
    data: Any,
    *,
    split: str,
    window_size: int,
    stride: int,
    feature_config: Any,
    max_interpolation_gap: int,
) -> WindowDataset:
    if isinstance(data, WindowDataset):
        return data
    if isinstance(data, Dataset):
        raise TypeError(
            "Training currently requires WindowDataset so input_size and class "
            "weights can be inferred safely"
        )
    return load_window_dataset(
        data,
        split=split,
        window_size=window_size,
        stride=stride,
        feature_config=feature_config,
        max_interpolation_gap=max_interpolation_gap,
    )


def _checkpoint_payload(
    *,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    best_epoch: int,
    best_validation_loss: float,
    model_spec: Mapping[str, Any],
    config: Mapping[str, Any],
    model_version: str,
    posture_weights: torch.Tensor | None,
    positive_weight: torch.Tensor | None,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "checkpoint_schema_version": 1,
        "epoch": epoch,
        "best_epoch": best_epoch,
        "best_validation_loss": best_validation_loss,
        "model_version": model_version,
        "model_spec": dict(model_spec),
        "input_size": model_spec["input_size"],
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "posture_class_weights": (
            posture_weights.tolist() if posture_weights is not None else None
        ),
        "event_positive_weight": (
            float(positive_weight.item()) if positive_weight is not None else None
        ),
        "config": dict(config),
        "history": list(history),
    }


def _model_spec(model: nn.Module, config: Any, input_size: int) -> dict[str, Any]:
    if isinstance(model, MultiTaskTemporalModel):
        return model.checkpoint_spec()
    values = {
        "architecture": _config_value(config, "model.architecture", "gru"),
        "input_size": input_size,
        "hidden_size": _config_value(config, "model.hidden_size", 96),
        "num_layers": _config_value(config, "model.num_layers", 2),
        "dropout": _config_value(config, "model.dropout", 0.30),
        "posture_classes": _config_value(config, "model.posture_classes", 3),
    }
    return _json_safe(values)


def _config_value(config: Any, path: str, default: Any) -> Any:
    current = config
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return default
    return current


def _nested_config(config: Any, name: str) -> Any:
    if isinstance(config, Mapping):
        return config.get(name, {})
    return getattr(config, name, config)


def _serialize_config(config: Any) -> dict[str, Any]:
    if hasattr(config, "model_dump"):
        try:
            return _json_safe(config.model_dump(mode="json"))
        except TypeError:
            return _json_safe(config.model_dump())
    if isinstance(config, Mapping):
        return _json_safe(dict(config))
    if hasattr(config, "__dict__"):
        return _json_safe(vars(config))
    raise TypeError("config must be a mapping or structured configuration object")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().tolist()
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _json_safe(value.value)
    return value


def _counts(labels: torch.Tensor, classes: int) -> dict[str, int]:
    counts = torch.bincount(labels.reshape(-1).long(), minlength=classes)
    return {str(index): int(counts[index]) for index in range(classes)}


def _atomic_torch_save(payload: Mapping[str, Any], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(dict(payload), temporary)
    temporary.replace(path)


def _torch_load(path: Path) -> Mapping[str, Any]:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:  # PyTorch < 2.6
        return torch.load(path, map_location="cpu")


def _write_yaml(payload: Mapping[str, Any], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_json(payload: Mapping[str, Any], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_history(history: list[dict[str, Any]], path: Path) -> None:
    if not history:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    temporary.replace(path)
