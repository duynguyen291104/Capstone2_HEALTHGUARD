from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from torch import nn

from fallguard.models.temporal import TemporalOutput
from fallguard.training import (
    WindowDataset,
    calibrate_event_threshold,
    load_calibrated_threshold,
)


class _ScoreModel(nn.Module):
    input_size = 1
    posture_classes = 3

    def forward(self, features: torch.Tensor) -> TemporalOutput:
        event_logits = features[:, -1, 0]
        posture_logits = torch.zeros(
            (features.shape[0], 3),
            dtype=features.dtype,
            device=features.device,
        )
        return TemporalOutput(posture_logits, event_logits)


class _MustNotRunModel(_ScoreModel):
    def forward(self, features: torch.Tensor) -> TemporalOutput:
        raise AssertionError("test-labelled data must be rejected before inference")


def _validation_dataset(*, include_sequences: bool = True) -> WindowDataset:
    probabilities = np.asarray([0.10, 0.40, 0.90, 0.20, 0.10, 0.20])
    logits = np.log(probabilities / (1.0 - probabilities)).astype(np.float32)
    features = np.repeat(logits[:, None, None], 3, axis=1)
    labels = np.asarray([0, 1, 1, 0, 0, 0])
    metadata: dict[str, np.ndarray] = {
        "split": np.repeat("validation", len(labels)),
    }
    if include_sequences:
        metadata.update(
            {
                "sequence_id": np.asarray(["fall"] * 4 + ["adl"] * 2),
                "video_id": np.asarray(["fall"] * 4 + ["adl"] * 2),
                "timestamp_ms": np.asarray([0, 100, 200, 300, 0, 100]),
            }
        )
    return WindowDataset(
        features,
        posture_labels=np.zeros(len(labels), dtype=np.int64),
        event_labels=labels,
        metadata=metadata,
    )


def test_calibration_selects_validation_frame_f1_and_writes_provenance(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "calibration.json"

    result = calibrate_event_threshold(
        _ScoreModel(),
        _validation_dataset(),
        objective="frame_f1",
        thresholds=[0.3, 0.5, 0.8],
        device="cpu",
        output_path=destination,
    )

    assert result["chosen_threshold"] == pytest.approx(0.3)
    assert result["objective_score"] == 1.0
    assert result["chosen_metrics"]["frame"]["precision"] == 1.0
    assert result["chosen_metrics"]["frame"]["recall"] == 1.0
    assert load_calibrated_threshold(destination) == pytest.approx(0.3)
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["provenance"]["data_role"] == "validation_only"
    assert saved["provenance"]["test_data_accessed"] is False
    assert saved["candidate_count"] == 3


def test_pr_curve_calibration_supports_frame_objective_without_sequence_ids(
    tmp_path: Path,
) -> None:
    result = calibrate_event_threshold(
        _ScoreModel(),
        _validation_dataset(include_sequences=False),
        objective="frame_f1",
        method="pr_curve",
        device="cpu",
        output_path=tmp_path,
    )

    assert (tmp_path / "calibration.json").is_file()
    assert result["chosen_metrics"]["event"]["available"] is False
    assert result["candidate_count"] >= 4


def test_event_objective_uses_contiguous_validation_events(tmp_path: Path) -> None:
    probabilities = np.asarray([0.20, 0.20, 0.20, 0.40, 0.40])
    logits = np.log(probabilities / (1.0 - probabilities)).astype(np.float32)
    dataset = WindowDataset(
        np.repeat(logits[:, None, None], 2, axis=1),
        posture_labels=np.zeros(5, dtype=np.int64),
        event_labels=np.asarray([0, 1, 1, 0, 0]),
        metadata={
            "split": np.repeat("validation", 5),
            "sequence_id": np.repeat("fall-video", 5),
            "timestamp_ms": np.arange(5) * 100,
        },
    )

    result = calibrate_event_threshold(
        _ScoreModel(),
        dataset,
        objective="event_f1",
        thresholds=[0.1, 0.3, 0.5],
        device="cpu",
        output_path=tmp_path,
    )

    assert result["chosen_threshold"] == pytest.approx(0.1)
    assert result["chosen_metrics"]["event"]["f1"] == 1.0
    assert result["chosen_metrics"]["frame"]["precision"] < 1.0


def test_event_objective_requires_sequence_metadata(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sequence_id"):
        calibrate_event_threshold(
            _ScoreModel(),
            _validation_dataset(include_sequences=False),
            objective="event_f1",
            device="cpu",
            output_path=tmp_path,
        )


def test_calibration_rejects_test_path_before_loading_labels(tmp_path: Path) -> None:
    test_path = tmp_path / "windows_test.npz"
    test_path.touch()

    with pytest.raises(ValueError, match="validation-only"):
        calibrate_event_threshold(
            _MustNotRunModel(),
            test_path,
            objective="frame_f1",
            output_path=tmp_path / "calibration.json",
        )


def test_calibration_rejects_in_memory_test_metadata_before_model_call(
    tmp_path: Path,
) -> None:
    dataset = _validation_dataset()
    dataset.metadata["split"] = np.repeat("test", len(dataset))

    with pytest.raises(ValueError, match="validation-only"):
        calibrate_event_threshold(
            _MustNotRunModel(),
            dataset,
            objective="frame_f1",
            output_path=tmp_path,
        )
