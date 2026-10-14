"""Pose normalization, interpolation, feature extraction, and causal windows."""

from fallguard.features.materialize import (
    WindowFileInfo,
    WindowMaterializationReport,
    materialize_window_datasets,
    prepare_window_datasets,
)
from fallguard.features.preprocessing import (
    FeatureSequence,
    InterpolationResult,
    NormalizationResult,
    build_frame_features,
    extract_features,
    geometric_features,
    interpolate_short_gaps,
    normalize_landmarks,
)
from fallguard.features.windows import (
    CausalWindowBatch,
    build_causal_windows,
    concatenate_window_batches,
    create_windows,
)

__all__ = [
    "CausalWindowBatch",
    "FeatureSequence",
    "InterpolationResult",
    "NormalizationResult",
    "WindowFileInfo",
    "WindowMaterializationReport",
    "build_causal_windows",
    "build_frame_features",
    "concatenate_window_batches",
    "create_windows",
    "extract_features",
    "geometric_features",
    "interpolate_short_gaps",
    "materialize_window_datasets",
    "normalize_landmarks",
    "prepare_window_datasets",
]
