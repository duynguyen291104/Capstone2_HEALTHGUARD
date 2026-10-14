"""Dataset loading, deterministic training, and honest evaluation metrics."""

from fallguard.training.calibration import (
    calibrate_event_threshold,
    load_calibrated_threshold,
)
from fallguard.training.data import (
    WindowDataset,
    build_dataloader,
    infer_input_size,
    load_split_datasets,
    load_window_dataset,
)
from fallguard.training.evaluate import (
    evaluate,
    evaluate_checkpoint,
    evaluate_model,
    resolve_device,
)
from fallguard.training.losses import (
    LossOutput,
    MultiTaskLoss,
    balanced_class_weights,
    compute_class_weights,
    event_positive_weight,
)
from fallguard.training.metrics import (
    binary_event_metrics,
    classification_metrics,
    compute_event_level_metrics,
    evaluate_predictions,
    event_level_metrics,
)
from fallguard.training.trainer import (
    Trainer,
    TrainingResult,
    seed_everything,
    set_deterministic,
    train,
    train_model,
)

__all__ = [
    "LossOutput",
    "MultiTaskLoss",
    "Trainer",
    "TrainingResult",
    "WindowDataset",
    "balanced_class_weights",
    "binary_event_metrics",
    "build_dataloader",
    "calibrate_event_threshold",
    "classification_metrics",
    "compute_class_weights",
    "compute_event_level_metrics",
    "evaluate",
    "evaluate_checkpoint",
    "evaluate_model",
    "evaluate_predictions",
    "event_level_metrics",
    "event_positive_weight",
    "infer_input_size",
    "load_split_datasets",
    "load_calibrated_threshold",
    "load_window_dataset",
    "resolve_device",
    "seed_everything",
    "set_deterministic",
    "train",
    "train_model",
]
