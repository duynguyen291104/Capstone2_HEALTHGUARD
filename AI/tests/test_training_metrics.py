from __future__ import annotations

import numpy as np
import pytest

from fallguard.training.metrics import (
    classification_metrics,
    evaluate_predictions,
    event_level_metrics,
)


def test_classification_metrics_expose_per_class_and_confusion_matrix() -> None:
    report = classification_metrics(
        [0, 0, 1, 1, 2, 2],
        [0, 1, 1, 1, 2, 0],
        labels=(0, 1, 2),
        class_names=("UPRIGHT", "TRANSITION", "LYING"),
    )

    assert report["confusion_matrix"] == [[1, 1, 0], [0, 2, 0], [1, 0, 1]]
    assert report["classes"]["TRANSITION"]["recall"] == 1.0
    assert 0.0 <= report["macro_f1"] <= 1.0


def test_event_level_metrics_match_events_and_measure_delay_and_false_alarms() -> None:
    truth = np.asarray([0, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    prediction = np.asarray([0, 0, 1, 0, 1, 0, 0, 0, 0, 0])
    sequences = np.asarray(["a"] * 5 + ["b"] * 5)
    timestamps = np.tile(np.arange(5) * 1000, 2)

    report = event_level_metrics(
        truth,
        prediction,
        sequence_ids=sequences,
        timestamps_ms=timestamps,
    )

    assert report["true_positives"] == 1
    assert report["false_positives"] == 1
    assert report["false_negatives"] == 0
    assert report["precision"] == 0.5
    assert report["recall"] == 1.0
    assert report["f1"] == pytest.approx(2 / 3)
    assert report["false_alarms_per_video"] == 0.5
    assert report["detection_delay_seconds"]["mean"] == 1.0


def test_evaluation_marks_event_metrics_unavailable_without_sequence_identity() -> None:
    report = evaluate_predictions(
        posture_true=[0, 1, 2],
        posture_pred=[0, 1, 2],
        event_true=[0, 1, 0],
        event_probabilities=[0.1, 0.9, 0.2],
    )

    assert report["posture"]["macro_f1"] == 1.0
    assert report["event"]["f1"] == 1.0
    assert report["event_level"]["available"] is False
    assert "false_alarms_per_video" not in report["event_level"]
