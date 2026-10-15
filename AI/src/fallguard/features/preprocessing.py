from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28
NOSE = 0


@dataclass(frozen=True, slots=True)
class InterpolationResult:
    landmarks: np.ndarray
    observed_valid: np.ndarray
    usable_valid: np.ndarray
    interpolated: np.ndarray


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    landmarks: np.ndarray
    valid: np.ndarray
    centers: np.ndarray
    scales: np.ndarray


@dataclass(frozen=True, slots=True)
class FeatureSequence:
    values: np.ndarray
    frame_valid: np.ndarray
    feature_names: tuple[str, ...]
    observed_valid: np.ndarray
    usable_valid: np.ndarray
    interpolated: np.ndarray


def _validate_pose_arrays(
    landmarks: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(landmarks, dtype=np.float32)
    mask = np.asarray(valid, dtype=bool)
    if values.ndim != 3 or values.shape[1:] != (33, 4):
        raise ValueError(f"landmarks must have shape [T, 33, 4], got {values.shape}")
    if mask.shape != values.shape[:2]:
        raise ValueError(f"valid must have shape {values.shape[:2]}, got {mask.shape}")
    return values, mask


def interpolate_short_gaps(
    landmarks: np.ndarray,
    valid: np.ndarray,
    *,
    max_gap: int = 3,
) -> InterpolationResult:
    """Linearly fill only bounded per-joint gaps no longer than ``max_gap``.

    The original observation mask is retained separately. ``usable_valid`` adds
    points produced by interpolation, while ``interpolated`` identifies exactly
    those synthetic points.
    """

    if max_gap < 0:
        raise ValueError("max_gap must be non-negative")
    values, observed = _validate_pose_arrays(landmarks, valid)
    output = values.copy()
    observed = observed.copy()
    finite = np.isfinite(output).all(axis=2)
    observed &= finite
    usable = observed.copy()
    filled = np.zeros_like(observed)
    if max_gap == 0 or len(output) < 3:
        output[~usable] = 0.0
        return InterpolationResult(output, observed, usable, filled)

    for joint in range(33):
        index = 0
        while index < len(output):
            if observed[index, joint]:
                index += 1
                continue
            start = index
            while index < len(output) and not observed[index, joint]:
                index += 1
            end = index
            gap = end - start
            if (
                gap <= max_gap
                and start > 0
                and end < len(output)
                and observed[start - 1, joint]
                and observed[end, joint]
            ):
                left = output[start - 1, joint].astype(np.float64)
                right = output[end, joint].astype(np.float64)
                for offset, frame_index in enumerate(range(start, end), start=1):
                    alpha = offset / (gap + 1)
                    output[frame_index, joint] = (
                        (1.0 - alpha) * left + alpha * right
                    ).astype(np.float32)
                usable[start:end, joint] = True
                filled[start:end, joint] = True

    output[~usable] = 0.0
    return InterpolationResult(output, observed, usable, filled)


def _pair_center(
    coordinates: np.ndarray, mask: np.ndarray, left: int, right: int
) -> tuple[np.ndarray, np.ndarray]:
    both = mask[:, left] & mask[:, right]
    center = (coordinates[:, left] + coordinates[:, right]) * 0.5
    return center, both


def normalize_landmarks(
    landmarks: np.ndarray,
    valid: np.ndarray,
    *,
    epsilon: float = 1e-6,
) -> NormalizationResult:
    """Hip-center and torso-scale a pose sequence frame by frame.

    Torso scale is the Euclidean distance between shoulder and hip centers.
    Frames missing either hip use a sequence median hip center offset only when
    one hip is visible; scale falls back to the median valid torso scale. Points
    that remain invalid are exactly zero.
    """

    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    values, mask = _validate_pose_arrays(landmarks, valid)
    coordinates = values[..., :3].astype(np.float32, copy=True)
    mask = mask & np.isfinite(values).all(axis=2)
    time_steps = len(values)

    hip_center, both_hips = _pair_center(
        coordinates, mask, LEFT_HIP, RIGHT_HIP
    )
    shoulder_center, both_shoulders = _pair_center(
        coordinates, mask, LEFT_SHOULDER, RIGHT_SHOULDER
    )

    # A one-hip fallback is better than discarding a complete frame. Estimate
    # half the hip vector from frames where both hips are observed.
    hip_vector = (coordinates[:, RIGHT_HIP] - coordinates[:, LEFT_HIP]) * 0.5
    if both_hips.any():
        median_half_hip = np.median(hip_vector[both_hips], axis=0)
    else:
        median_half_hip = np.zeros(3, dtype=np.float32)
    left_only = mask[:, LEFT_HIP] & ~mask[:, RIGHT_HIP]
    right_only = mask[:, RIGHT_HIP] & ~mask[:, LEFT_HIP]
    hip_center[left_only] = coordinates[left_only, LEFT_HIP] + median_half_hip
    hip_center[right_only] = coordinates[right_only, RIGHT_HIP] - median_half_hip
    center_valid = both_hips | left_only | right_only

    scale_valid = center_valid & both_shoulders
    scales = np.linalg.norm(shoulder_center - hip_center, axis=1).astype(np.float32)
    scale_valid &= np.isfinite(scales) & (scales > epsilon)
    median_scale = (
        float(np.median(scales[scale_valid])) if scale_valid.any() else 1.0
    )
    if median_scale <= epsilon or not np.isfinite(median_scale):
        median_scale = 1.0
    scales[~scale_valid] = median_scale

    normalized = np.zeros_like(values, dtype=np.float32)
    normalized[..., 3] = np.where(mask, values[..., 3], 0.0)
    valid_output = mask & center_valid[:, None]
    centered = (coordinates - hip_center[:, None, :]) / scales[:, None, None]
    normalized[..., :3] = np.where(valid_output[..., None], centered, 0.0)
    normalized[~valid_output] = 0.0
    centers = np.where(center_valid[:, None], hip_center, 0.0).astype(np.float32)
    scales = np.where(center_valid, scales, 1.0).astype(np.float32)

    if time_steps == 0:
        centers = np.empty((0, 3), dtype=np.float32)
        scales = np.empty((0,), dtype=np.float32)
    return NormalizationResult(normalized, valid_output, centers, scales)


def _safe_angle(first: np.ndarray, vertex: np.ndarray, third: np.ndarray) -> np.ndarray:
    left = first - vertex
    right = third - vertex
    denominator = np.linalg.norm(left, axis=1) * np.linalg.norm(right, axis=1)
    dot = np.einsum("ij,ij->i", left, right)
    cosine = np.divide(
        dot,
        denominator,
        out=np.zeros_like(dot, dtype=np.float32),
        where=denominator > 1e-8,
    )
    return np.arccos(np.clip(cosine, -1.0, 1.0)).astype(np.float32)


def geometric_features(
    normalized_landmarks: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Compute compact body geometry from normalized MediaPipe landmarks."""

    values, mask = _validate_pose_arrays(normalized_landmarks, valid)
    xyz = values[..., :3]
    shoulder_center = (xyz[:, LEFT_SHOULDER] + xyz[:, RIGHT_SHOULDER]) * 0.5
    hip_center = (xyz[:, LEFT_HIP] + xyz[:, RIGHT_HIP]) * 0.5
    torso = shoulder_center - hip_center
    torso_norm = np.linalg.norm(torso[:, :2], axis=1)
    torso_vertical_angle = np.arctan2(np.abs(torso[:, 0]), np.abs(torso[:, 1]) + 1e-8)
    shoulder_width = np.linalg.norm(
        xyz[:, LEFT_SHOULDER] - xyz[:, RIGHT_SHOULDER], axis=1
    )
    hip_width = np.linalg.norm(xyz[:, LEFT_HIP] - xyz[:, RIGHT_HIP], axis=1)
    left_knee_angle = _safe_angle(
        xyz[:, LEFT_HIP], xyz[:, LEFT_KNEE], xyz[:, LEFT_ANKLE]
    )
    right_knee_angle = _safe_angle(
        xyz[:, RIGHT_HIP], xyz[:, RIGHT_KNEE], xyz[:, RIGHT_ANKLE]
    )
    nose_relative_y = xyz[:, NOSE, 1]
    ankle_mean_y = (xyz[:, LEFT_ANKLE, 1] + xyz[:, RIGHT_ANKLE, 1]) * 0.5

    geometry = np.stack(
        (
            torso_vertical_angle,
            torso_norm,
            shoulder_width,
            hip_width,
            left_knee_angle,
            right_knee_angle,
            nose_relative_y,
            ankle_mean_y,
        ),
        axis=1,
    ).astype(np.float32)
    required = np.stack(
        (
            mask[:, LEFT_SHOULDER] & mask[:, RIGHT_SHOULDER],
            mask[:, LEFT_SHOULDER]
            & mask[:, RIGHT_SHOULDER]
            & mask[:, LEFT_HIP]
            & mask[:, RIGHT_HIP],
            mask[:, LEFT_SHOULDER] & mask[:, RIGHT_SHOULDER],
            mask[:, LEFT_HIP] & mask[:, RIGHT_HIP],
            mask[:, LEFT_HIP] & mask[:, LEFT_KNEE] & mask[:, LEFT_ANKLE],
            mask[:, RIGHT_HIP] & mask[:, RIGHT_KNEE] & mask[:, RIGHT_ANKLE],
            mask[:, NOSE],
            mask[:, LEFT_ANKLE] & mask[:, RIGHT_ANKLE],
        ),
        axis=1,
    )
    geometry[~required] = 0.0
    names = (
        "torso_vertical_angle",
        "torso_length",
        "shoulder_width",
        "hip_width",
        "left_knee_angle",
        "right_knee_angle",
        "nose_relative_y",
        "ankle_mean_y",
    )
    return geometry, names


def build_frame_features(
    landmarks: np.ndarray,
    valid: np.ndarray,
    *,
    max_interpolation_gap: int = 3,
    include_xyz: bool = True,
    include_visibility: bool = True,
    include_velocity: bool = True,
    include_geometry: bool = True,
) -> FeatureSequence:
    """Interpolate, normalize, and assemble deterministic per-frame features."""

    if not any((include_xyz, include_visibility, include_velocity, include_geometry)):
        raise ValueError("At least one feature family must be enabled")
    interpolated = interpolate_short_gaps(
        landmarks, valid, max_gap=max_interpolation_gap
    )
    normalized = normalize_landmarks(interpolated.landmarks, interpolated.usable_valid)
    blocks: list[np.ndarray] = []
    names: list[str] = []

    if include_xyz:
        blocks.append(normalized.landmarks[..., :3].reshape(len(landmarks), -1))
        names.extend(
            f"joint_{joint:02d}_{axis}"
            for joint in range(33)
            for axis in ("x", "y", "z")
        )
    if include_visibility:
        visibility = interpolated.landmarks[..., 3].copy()
        visibility[~interpolated.usable_valid] = 0.0
        blocks.append(visibility)
        names.extend(f"joint_{joint:02d}_visibility" for joint in range(33))
    if include_velocity:
        xyz = normalized.landmarks[..., :3]
        velocity = np.zeros_like(xyz)
        if len(xyz) > 1:
            pair_valid = normalized.valid[1:] & normalized.valid[:-1]
            velocity[1:] = xyz[1:] - xyz[:-1]
            velocity[1:][~pair_valid] = 0.0
        blocks.append(velocity.reshape(len(landmarks), -1))
        names.extend(
            f"joint_{joint:02d}_velocity_{axis}"
            for joint in range(33)
            for axis in ("x", "y", "z")
        )
    if include_geometry:
        geometry, geometry_names = geometric_features(
            normalized.landmarks, normalized.valid
        )
        blocks.append(geometry)
        names.extend(geometry_names)

    values = np.concatenate(blocks, axis=1).astype(np.float32, copy=False)
    frame_valid = normalized.valid.any(axis=1)
    return FeatureSequence(
        values=values,
        frame_valid=frame_valid,
        feature_names=tuple(names),
        observed_valid=interpolated.observed_valid,
        usable_valid=interpolated.usable_valid,
        interpolated=interpolated.interpolated,
    )


# Concise aliases used by training/runtime callers.
extract_features = build_frame_features
