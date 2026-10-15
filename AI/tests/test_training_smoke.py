from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from fallguard.models import TemporalPredictor
from fallguard.training import WindowDataset, train_model


def _tiny_config() -> dict:
    return {
        "project": {"seed": 17, "device": "cpu"},
        "data": {"window_size": 6, "stride": 1},
        "model": {
            "architecture": "gru",
            "hidden_size": 8,
            "num_layers": 1,
            "dropout": 0.0,
            "posture_classes": 3,
            "posture_loss_weight": 0.3,
        },
        "training": {
            "batch_size": 6,
            "epochs": 1,
            "learning_rate": 0.005,
            "weight_decay": 0.0,
            "patience": 1,
            "num_workers": 0,
            "weighted_loss": True,
        },
    }


def _synthetic_dataset(seed: int) -> WindowDataset:
    generator = np.random.default_rng(seed)
    labels = np.tile(np.arange(3), 8)
    events = (labels == 2).astype(np.int64)
    features = generator.normal(scale=0.05, size=(24, 6, 5)).astype(np.float32)
    features[:, :, 0] += labels[:, None]
    features[:, :, 1] += events[:, None]
    sequence_ids = np.repeat([f"video-{index}" for index in range(4)], 6)
    timestamps = np.tile(np.arange(6) * 250, 4)
    return WindowDataset(
        features,
        labels,
        events,
        {
            "sequence_id": sequence_ids,
            "video_id": sequence_ids,
            "timestamp_ms": timestamps,
        },
    )


def test_one_epoch_cpu_training_emits_complete_artifact_contract(tmp_path: Path) -> None:
    train_dataset = _synthetic_dataset(1)
    validation_dataset = _synthetic_dataset(2)

    result = train_model(
        _tiny_config(),
        train_dataset,
        validation_dataset,
        tmp_path,
    )

    expected = {
        "best.pt",
        "last.pt",
        "config.yaml",
        "metadata.json",
        "history.csv",
        "metrics.json",
    }
    assert expected == {path.name for path in tmp_path.iterdir()}
    assert result.best_epoch == 1
    assert result.epochs_completed == 1
    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metadata["input_size"] == 5
    assert metadata["training_samples"] == 24
    assert metrics["sample_count"] == 24
    assert "confusion_matrix" in metrics["posture"]
    assert "false_alarms_per_video" in metrics["event_level"]
    with (tmp_path / "history.csv").open(newline="", encoding="utf-8") as handle:
        history = list(csv.DictReader(handle))
    assert len(history) == 1
    assert "validation_total_loss" in history[0]

    classifier = TemporalPredictor.from_checkpoint(tmp_path / "best.pt", device="cpu")
    prediction = classifier.predict(validation_dataset.features[0])
    assert len(prediction.posture_probabilities) == 3
