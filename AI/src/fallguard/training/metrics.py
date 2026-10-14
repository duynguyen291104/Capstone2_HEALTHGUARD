from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from fallguard.domain.enums import PostureLabel


def classification_metrics(
    y_true: Sequence[int] | np.ndarray,
    y_pred: Sequence[int] | np.ndarray,
    *,
    labels: Sequence[int],
    class_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """JSON-serializable precision/recall/F1 and confusion matrix."""

    truth = _integer_vector(y_true, "y_true")
    predictions = _integer_vector(y_pred, "y_pred")
    if truth.shape != predictions.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    label_values = [int(label) for label in labels]
    if not label_values:
        raise ValueError("labels must not be empty")
    names = (
        [str(name) for name in class_names]
        if class_names is not None
        else [str(label) for label in label_values]
    )
    if len(names) != len(label_values):
        raise ValueError("class_names and labels must have the same length")
    accepted = np.asarray(label_values)
    if not np.all(np.isin(truth, accepted)):
        raise ValueError("y_true contains labels outside the requested label set")
    if not np.all(np.isin(predictions, accepted)):
        raise ValueError("y_pred contains labels outside the requested label set")

    label_to_index = {label: index for index, label in enumerate(label_values)}
    confusion = np.zeros((len(label_values), len(label_values)), dtype=np.int64)
    for actual, predicted in zip(truth, predictions, strict=True):
        if int(actual) in label_to_index and int(predicted) in label_to_index:
            confusion[label_to_index[int(actual)], label_to_index[int(predicted)]] += 1

    true_positives = np.diag(confusion).astype(np.float64)
    predicted_counts = confusion.sum(axis=0).astype(np.float64)
    actual_counts = confusion.sum(axis=1).astype(np.float64)
    precision = _safe_divide(true_positives, predicted_counts)
    recall = _safe_divide(true_positives, actual_counts)
    f1 = _safe_divide(2.0 * precision * recall, precision + recall)
    support = actual_counts.astype(np.int64)
    total = int(confusion.sum())
    accuracy = float(true_positives.sum() / total) if total else 0.0
    macro = {
        "precision": float(np.mean(precision)),
        "recall": float(np.mean(recall)),
        "f1": float(np.mean(f1)),
        "support": total,
    }
    if total:
        weighted = {
            "precision": float(np.average(precision, weights=support)),
            "recall": float(np.average(recall, weights=support)),
            "f1": float(np.average(f1, weights=support)),
            "support": total,
        }
    else:
        weighted = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}

    per_class = {
        name: {
            "label": label,
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        for index, (label, name) in enumerate(zip(label_values, names, strict=True))
    }
    return {
        "accuracy": accuracy,
        "precision": precision.tolist(),
        "recall": recall.tolist(),
        "f1": f1.tolist(),
        "support": support.tolist(),
        "classes": per_class,
        "macro_avg": macro,
        "weighted_avg": weighted,
        "macro_precision": macro["precision"],
        "macro_recall": macro["recall"],
        "macro_f1": macro["f1"],
        "confusion_matrix": confusion.tolist(),
        "confusion_matrix_labels": names,
        "sample_count": int(truth.size),
    }


def binary_event_metrics(
    y_true: Sequence[int] | np.ndarray,
    y_pred: Sequence[int] | np.ndarray,
) -> dict[str, Any]:
    truth = _binary_vector(y_true, "y_true")
    predictions = _binary_vector(y_pred, "y_pred")
    report = classification_metrics(
        truth,
        predictions,
        labels=(0, 1),
        class_names=("NON_EVENT", "FALL_EVENT"),
    )
    positive = report["classes"]["FALL_EVENT"]
    return {
        **report,
        "precision_per_class": report["precision"],
        "recall_per_class": report["recall"],
        "f1_per_class": report["f1"],
        "precision_positive": positive["precision"],
        "recall_positive": positive["recall"],
        "f1_positive": positive["f1"],
        # Concise aliases make the positive fall class the default binary
        # interpretation while preserving all per-class values above.
        "precision": positive["precision"],
        "recall": positive["recall"],
        "f1": positive["f1"],
    }


def evaluate_predictions(
    posture_true: Sequence[int] | np.ndarray,
    posture_pred: Sequence[int] | np.ndarray,
    event_true: Sequence[int] | np.ndarray,
    event_probabilities: Sequence[float] | np.ndarray,
    *,
    event_threshold: float = 0.5,
    sequence_ids: Sequence[Any] | np.ndarray | None = None,
    video_ids: Sequence[Any] | np.ndarray | None = None,
    timestamps_ms: Sequence[int] | np.ndarray | None = None,
    frame_indices: Sequence[int] | np.ndarray | None = None,
    posture_class_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    probabilities = np.asarray(event_probabilities, dtype=np.float64).reshape(-1)
    if not np.isfinite(probabilities).all():
        raise ValueError("event_probabilities contain NaN or infinite values")
    if np.any((probabilities < 0.0) | (probabilities > 1.0)):
        raise ValueError("event_probabilities must be in [0, 1]")
    if not 0.0 <= event_threshold <= 1.0:
        raise ValueError("event_threshold must be in [0, 1]")
    event_predictions = (probabilities >= event_threshold).astype(np.int64)
    class_names = posture_class_names or tuple(label.name for label in PostureLabel)
    posture_labels = tuple(range(len(class_names)))

    metrics: dict[str, Any] = {
        "posture": classification_metrics(
            posture_true,
            posture_pred,
            labels=posture_labels,
            class_names=class_names,
        ),
        "event": binary_event_metrics(event_true, event_predictions),
        "event_threshold": float(event_threshold),
    }
    if sequence_ids is not None:
        metrics["event_level"] = event_level_metrics(
            event_true,
            event_predictions,
            sequence_ids=sequence_ids,
            video_ids=video_ids,
            timestamps_ms=timestamps_ms,
            frame_indices=frame_indices,
        )
    else:
        metrics["event_level"] = {
            "available": False,
            "reason": "sequence_ids were not provided",
        }
    return metrics


@dataclass(slots=True, frozen=True)
class _Run:
    group: str
    start_position: int
    end_position: int
    start_timestamp_ms: float | None
    end_timestamp_ms: float | None
    start_frame: int | None
    end_frame: int | None


def event_level_metrics(
    y_true: Sequence[int] | np.ndarray,
    y_pred: Sequence[int] | np.ndarray,
    *,
    sequence_ids: Sequence[Any] | np.ndarray,
    video_ids: Sequence[Any] | np.ndarray | None = None,
    timestamps_ms: Sequence[int] | np.ndarray | None = None,
    frame_indices: Sequence[int] | np.ndarray | None = None,
) -> dict[str, Any]:
    """Match contiguous predicted and true fall regions one-to-one per sequence."""

    truth = _binary_vector(y_true, "y_true")
    predictions = _binary_vector(y_pred, "y_pred")
    if truth.shape != predictions.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    sample_count = truth.size
    groups = _metadata_vector(sequence_ids, sample_count, "sequence_ids").astype(str)
    videos = (
        _metadata_vector(video_ids, sample_count, "video_ids").astype(str)
        if video_ids is not None
        else groups
    )
    times = (
        _metadata_vector(timestamps_ms, sample_count, "timestamps_ms").astype(np.float64)
        if timestamps_ms is not None
        else None
    )
    frames = (
        _metadata_vector(frame_indices, sample_count, "frame_indices").astype(np.int64)
        if frame_indices is not None
        else None
    )

    actual_runs: list[_Run] = []
    predicted_runs: list[_Run] = []
    for group in dict.fromkeys(groups.tolist()):
        positions = np.flatnonzero(groups == group)
        if times is not None:
            positions = positions[np.argsort(times[positions], kind="stable")]
        elif frames is not None:
            positions = positions[np.argsort(frames[positions], kind="stable")]
        actual_runs.extend(
            _contiguous_runs(
                truth,
                positions,
                group,
                timestamps_ms=times,
                frame_indices=frames,
            )
        )
        predicted_runs.extend(
            _contiguous_runs(
                predictions,
                positions,
                group,
                timestamps_ms=times,
                frame_indices=frames,
            )
        )

    matched_actual: set[int] = set()
    matched_pairs: list[tuple[_Run, _Run]] = []
    for predicted in predicted_runs:
        candidates = [
            (index, actual)
            for index, actual in enumerate(actual_runs)
            if index not in matched_actual
            and actual.group == predicted.group
            and _runs_overlap(actual, predicted)
        ]
        if not candidates:
            continue
        actual_index, actual = max(
            candidates,
            key=lambda item: _overlap_size(item[1], predicted),
        )
        matched_actual.add(actual_index)
        matched_pairs.append((actual, predicted))

    true_positive = len(matched_pairs)
    false_positive = len(predicted_runs) - true_positive
    false_negative = len(actual_runs) - true_positive
    precision = _scalar_divide(true_positive, true_positive + false_positive)
    recall = _scalar_divide(true_positive, true_positive + false_negative)
    f1 = _scalar_divide(2.0 * precision * recall, precision + recall)
    result: dict[str, Any] = {
        "available": True,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": true_positive,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "ground_truth_events": len(actual_runs),
        "predicted_events": len(predicted_runs),
        "missed_fall_rate": _scalar_divide(false_negative, len(actual_runs)),
        "videos": int(len(np.unique(videos))),
        "false_alarms_per_video": _scalar_divide(
            false_positive,
            len(np.unique(videos)),
        ),
        "video_unit": "video_id" if video_ids is not None else "sequence_id",
    }

    if times is not None:
        delays = [
            (predicted.start_timestamp_ms - actual.start_timestamp_ms) / 1000.0
            for actual, predicted in matched_pairs
            if actual.start_timestamp_ms is not None and predicted.start_timestamp_ms is not None
        ]
        if delays:
            result["detection_delay_seconds"] = {
                "mean": float(np.mean(delays)),
                "median": float(np.median(delays)),
                "minimum": float(np.min(delays)),
                "maximum": float(np.max(delays)),
                "values": [float(delay) for delay in delays],
                "matched_events": len(delays),
            }
    if frames is not None:
        delays_frames = [
            predicted.start_frame - actual.start_frame
            for actual, predicted in matched_pairs
            if actual.start_frame is not None and predicted.start_frame is not None
        ]
        if delays_frames:
            result["detection_delay_frames"] = {
                "mean": float(np.mean(delays_frames)),
                "median": float(np.median(delays_frames)),
                "values": [int(delay) for delay in delays_frames],
                "matched_events": len(delays_frames),
            }
    return result


compute_event_level_metrics = event_level_metrics


def _contiguous_runs(
    labels: np.ndarray,
    positions: np.ndarray,
    group: str,
    *,
    timestamps_ms: np.ndarray | None,
    frame_indices: np.ndarray | None,
) -> list[_Run]:
    runs: list[_Run] = []
    run_positions: list[int] = []
    for position in positions:
        if labels[position] == 1:
            run_positions.append(int(position))
        elif run_positions:
            runs.append(
                _make_run(
                    group,
                    run_positions,
                    timestamps_ms=timestamps_ms,
                    frame_indices=frame_indices,
                )
            )
            run_positions = []
    if run_positions:
        runs.append(
            _make_run(
                group,
                run_positions,
                timestamps_ms=timestamps_ms,
                frame_indices=frame_indices,
            )
        )
    return runs


def _make_run(
    group: str,
    positions: Sequence[int],
    *,
    timestamps_ms: np.ndarray | None,
    frame_indices: np.ndarray | None,
) -> _Run:
    start = positions[0]
    end = positions[-1]
    return _Run(
        group=group,
        start_position=int(start),
        end_position=int(end),
        start_timestamp_ms=float(timestamps_ms[start]) if timestamps_ms is not None else None,
        end_timestamp_ms=float(timestamps_ms[end]) if timestamps_ms is not None else None,
        start_frame=int(frame_indices[start]) if frame_indices is not None else None,
        end_frame=int(frame_indices[end]) if frame_indices is not None else None,
    )


def _runs_overlap(actual: _Run, predicted: _Run) -> bool:
    if actual.start_timestamp_ms is not None and predicted.start_timestamp_ms is not None:
        return (
            predicted.start_timestamp_ms <= actual.end_timestamp_ms
            and predicted.end_timestamp_ms >= actual.start_timestamp_ms
        )
    if actual.start_frame is not None and predicted.start_frame is not None:
        return (
            predicted.start_frame <= actual.end_frame and predicted.end_frame >= actual.start_frame
        )
    return (
        predicted.start_position <= actual.end_position
        and predicted.end_position >= actual.start_position
    )


def _overlap_size(actual: _Run, predicted: _Run) -> float:
    if actual.start_timestamp_ms is not None and predicted.start_timestamp_ms is not None:
        return max(
            0.0,
            min(actual.end_timestamp_ms, predicted.end_timestamp_ms)
            - max(actual.start_timestamp_ms, predicted.start_timestamp_ms)
            + 1.0,
        )
    if actual.start_frame is not None and predicted.start_frame is not None:
        return float(
            max(
                0,
                min(actual.end_frame, predicted.end_frame)
                - max(actual.start_frame, predicted.start_frame)
                + 1,
            )
        )
    return float(
        max(
            0,
            min(actual.end_position, predicted.end_position)
            - max(actual.start_position, predicted.start_position)
            + 1,
        )
    )


def _integer_vector(values: Sequence[int] | np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values)
    if array.ndim != 1:
        array = array.reshape(-1)
    if not np.isfinite(array.astype(np.float64)).all() or not np.all(array == np.floor(array)):
        raise ValueError(f"{name} must contain finite integer labels")
    return array.astype(np.int64, copy=False)


def _binary_vector(values: Sequence[int] | np.ndarray, name: str) -> np.ndarray:
    array = _integer_vector(values, name)
    if not np.all(np.isin(array, (0, 1))):
        raise ValueError(f"{name} must contain only 0 and 1")
    return array


def _metadata_vector(values: Any, sample_count: int, name: str) -> np.ndarray:
    array = np.asarray(values).reshape(-1)
    if array.shape != (sample_count,):
        raise ValueError(f"{name} must have shape ({sample_count},), got {array.shape}")
    return array


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=np.float64),
        where=denominator != 0,
    )


def _scalar_divide(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0
