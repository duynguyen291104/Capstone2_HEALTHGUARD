from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class CausalWindowBatch:
    windows: np.ndarray
    posture_targets: np.ndarray
    event_targets: np.ndarray
    validity: np.ndarray
    start_indices: np.ndarray
    end_indices: np.ndarray
    sequence_ids: np.ndarray

    def __post_init__(self) -> None:
        windows = np.asarray(self.windows, dtype=np.float32)
        posture = np.asarray(self.posture_targets, dtype=np.int64)
        event = np.asarray(self.event_targets, dtype=np.int64)
        validity = np.asarray(self.validity, dtype=bool)
        starts = np.asarray(self.start_indices, dtype=np.int64)
        ends = np.asarray(self.end_indices, dtype=np.int64)
        sequence_ids = np.asarray(self.sequence_ids, dtype=str)
        count = len(windows)
        if windows.ndim != 3:
            raise ValueError("windows must have shape [N, T, F]")
        if validity.shape != windows.shape[:2]:
            raise ValueError("validity must have shape [N, T]")
        if any(len(value) != count for value in (posture, event, starts, ends, sequence_ids)):
            raise ValueError("all window metadata arrays must have length N")
        if not np.isin(event, [0, 1]).all():
            raise ValueError("event_targets must contain only 0 or 1")
        object.__setattr__(self, "windows", windows)
        object.__setattr__(self, "posture_targets", posture)
        object.__setattr__(self, "event_targets", event)
        object.__setattr__(self, "validity", validity)
        object.__setattr__(self, "start_indices", starts)
        object.__setattr__(self, "end_indices", ends)
        object.__setattr__(self, "sequence_ids", sequence_ids)

    def __len__(self) -> int:
        return len(self.windows)


def build_causal_windows(
    features: np.ndarray,
    posture_labels: Sequence[int] | np.ndarray,
    event_labels: Sequence[int] | np.ndarray,
    *,
    frame_valid: Sequence[bool] | np.ndarray | None = None,
    window_size: int = 40,
    stride: int = 5,
    sequence_id: str = "",
    drop_unknown_targets: bool = True,
    unknown_posture_label: int = -100,
) -> CausalWindowBatch:
    """Build past-only windows whose target is exactly the final frame.

    For a window covering ``[start, end]``, no label or feature after ``end`` is
    read. This is the same causal contract used for real-time inference.
    """

    values = np.asarray(features, dtype=np.float32)
    posture = np.asarray(posture_labels, dtype=np.int64)
    event = np.asarray(event_labels, dtype=np.int64)
    if values.ndim != 2:
        raise ValueError(f"features must have shape [T, F], got {values.shape}")
    if posture.shape != (len(values),) or event.shape != (len(values),):
        raise ValueError("posture_labels/event_labels must have shape [T]")
    if not np.isin(event, [0, 1]).all():
        raise ValueError("event_labels must contain only 0 or 1")
    if window_size < 1 or stride < 1:
        raise ValueError("window_size and stride must be positive")
    validity = (
        np.ones(len(values), dtype=bool)
        if frame_valid is None
        else np.asarray(frame_valid, dtype=bool)
    )
    if validity.shape != (len(values),):
        raise ValueError("frame_valid must have shape [T]")

    starts: list[int] = []
    for start in range(0, max(0, len(values) - window_size + 1), stride):
        end = start + window_size - 1
        if drop_unknown_targets and posture[end] == unknown_posture_label:
            continue
        starts.append(start)

    count = len(starts)
    if count:
        windows = np.stack([values[start : start + window_size] for start in starts])
        valid_windows = np.stack(
            [validity[start : start + window_size] for start in starts]
        )
    else:
        windows = np.empty((0, window_size, values.shape[1]), dtype=np.float32)
        valid_windows = np.empty((0, window_size), dtype=bool)
    end_indices = np.asarray(
        [start + window_size - 1 for start in starts], dtype=np.int64
    )
    return CausalWindowBatch(
        windows=windows,
        posture_targets=posture[end_indices],
        event_targets=event[end_indices],
        validity=valid_windows,
        start_indices=np.asarray(starts, dtype=np.int64),
        end_indices=end_indices,
        sequence_ids=np.full(
            count,
            sequence_id,
            dtype=f"<U{max(1, len(sequence_id))}",
        ),
    )


def concatenate_window_batches(
    batches: Sequence[CausalWindowBatch],
) -> CausalWindowBatch:
    if not batches:
        raise ValueError("At least one batch is required")
    window_shapes = {batch.windows.shape[1:] for batch in batches}
    if len(window_shapes) != 1:
        raise ValueError(f"Window shapes differ: {sorted(window_shapes)}")
    return CausalWindowBatch(
        windows=np.concatenate([batch.windows for batch in batches], axis=0),
        posture_targets=np.concatenate(
            [batch.posture_targets for batch in batches], axis=0
        ),
        event_targets=np.concatenate([batch.event_targets for batch in batches], axis=0),
        validity=np.concatenate([batch.validity for batch in batches], axis=0),
        start_indices=np.concatenate([batch.start_indices for batch in batches], axis=0),
        end_indices=np.concatenate([batch.end_indices for batch in batches], axis=0),
        sequence_ids=np.concatenate([batch.sequence_ids for batch in batches], axis=0),
    )


# Backward-friendly concise name.
create_windows = build_causal_windows
