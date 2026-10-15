from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import requests

from fallguard.data.download import DownloadResult, download_file, safe_extract_zip
from fallguard.domain.enums import PostureLabel
from fallguard.domain.types import SequenceRecord

URFD_BASE_URL = "https://fenix.ur.edu.pl/~mkepski/ds/data"
URFD_LICENSE_NAME = "CC BY-NC-SA 4.0"
URFD_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
ANNOTATION_PROVENANCE = "urfd_depth_feature_derived"
IGNORE_LABEL = -100

URFD_FEATURE_COLUMNS = (
    "sequence",
    "frame",
    "label",
    "HeightWidthRatio",
    "MajorMinorRatio",
    "BoundingBoxOccupancy",
    "MaxStdXZ",
    "HHmaxRatio",
    "H",
    "D",
    "P40",
)
_FLOAT_COLUMNS = URFD_FEATURE_COLUMNS[3:]
_SEQUENCE_RE = re.compile(r"(?P<kind>fall|adl)[-_]?(?P<number>\d{1,2})", re.IGNORECASE)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


class LicenseNotAccepted(PermissionError):
    """Raised unless the caller explicitly accepts the official URFD license."""


@dataclass(frozen=True, slots=True)
class UrfdAsset:
    name: str
    url: str
    kind: Literal["rgb", "preview", "features"]
    sequence_id: str | None = None


@dataclass(frozen=True, slots=True)
class UrfdDownloadReport:
    root: Path
    selection: str
    profile: str
    sequence_ids: tuple[str, ...]
    downloads: tuple[DownloadResult, ...]
    extracted_directories: tuple[Path, ...]

    @property
    def downloaded_bytes(self) -> int:
        return sum(result.size_bytes for result in self.downloads if result.downloaded)


@dataclass(frozen=True, slots=True)
class DerivedAnnotations:
    posture_labels: np.ndarray
    event_labels: np.ndarray
    provenance: str = ANNOTATION_PROVENANCE

    def __post_init__(self) -> None:
        posture = np.asarray(self.posture_labels, dtype=np.int64)
        event = np.asarray(self.event_labels, dtype=np.int64)
        if posture.ndim != 1 or event.shape != posture.shape:
            raise ValueError("posture_labels and event_labels must be matching 1-D arrays")
        if not np.isin(event, [0, 1]).all():
            raise ValueError("event_labels must contain only 0 or 1")
        object.__setattr__(self, "posture_labels", posture)
        object.__setattr__(self, "event_labels", event)


def _require_license(accept_license: bool) -> None:
    if not accept_license:
        raise LicenseNotAccepted(
            "URFD is licensed CC BY-NC-SA 4.0 for non-commercial academic use. "
            "Re-run with explicit license acceptance after reviewing "
            f"{URFD_LICENSE_URL}"
        )


def _canonical_sequence_id(kind: str, number: int) -> str:
    return f"{kind.lower()}-{number:02d}"


def _all_sequence_ids() -> list[str]:
    return [f"fall-{index:02d}" for index in range(1, 31)] + [
        f"adl-{index:02d}" for index in range(1, 41)
    ]


def select_urfd_sequences(
    selection: Literal["smoke", "limit", "full"] = "smoke",
    *,
    limit: int | None = None,
) -> tuple[str, ...]:
    """Resolve the official sequence subset deterministically.

    ``smoke`` selects one fall and one ADL. ``limit`` interleaves falls and ADLs
    to avoid a prefix containing only positives. ``full`` selects all 70
    camera-0 streams.
    """

    if selection == "smoke":
        if limit is not None:
            raise ValueError("limit cannot be combined with selection='smoke'")
        return ("fall-01", "adl-01")
    if selection == "full":
        if limit is not None:
            raise ValueError("limit cannot be combined with selection='full'")
        return tuple(_all_sequence_ids())
    if selection != "limit":
        raise ValueError("selection must be one of: smoke, limit, full")
    if limit is None or not 1 <= limit <= 70:
        raise ValueError("selection='limit' requires limit in the range 1..70")

    interleaved: list[str] = []
    for index in range(1, 41):
        if index <= 30:
            interleaved.append(f"fall-{index:02d}")
        interleaved.append(f"adl-{index:02d}")
    return tuple(interleaved[:limit])


def official_urfd_assets(
    selection: Literal["smoke", "limit", "full"] = "smoke",
    *,
    limit: int | None = None,
    include_features: bool = True,
    profile: Literal["preview", "rgb"] = "rgb",
) -> tuple[UrfdAsset, ...]:
    sequence_ids = select_urfd_sequences(selection, limit=limit)
    if profile not in {"preview", "rgb"}:
        raise ValueError("profile must be 'preview' or 'rgb'")
    if profile == "rgb":
        assets = [
            UrfdAsset(
                name=f"{sequence_id}-cam0-rgb.zip",
                url=f"{URFD_BASE_URL}/{sequence_id}-cam0-rgb.zip",
                kind="rgb",
                sequence_id=sequence_id,
            )
            for sequence_id in sequence_ids
        ]
    else:
        assets = [
            UrfdAsset(
                name=f"{sequence_id}-cam0.mp4",
                url=f"{URFD_BASE_URL}/{sequence_id}-cam0.mp4",
                kind="preview",
                sequence_id=sequence_id,
            )
            for sequence_id in sequence_ids
        ]
    if include_features:
        assets.extend(
            (
                UrfdAsset(
                    name="urfall-cam0-falls.csv",
                    url=f"{URFD_BASE_URL}/urfall-cam0-falls.csv",
                    kind="features",
                ),
                UrfdAsset(
                    name="urfall-cam0-adls.csv",
                    url=f"{URFD_BASE_URL}/urfall-cam0-adls.csv",
                    kind="features",
                ),
            )
        )
    return tuple(assets)


def download_urfd_features(
    root: str | Path,
    *,
    accept_license: bool,
    retries: int = 4,
    session: requests.Session | None = None,
) -> tuple[DownloadResult, DownloadResult]:
    """Download both official camera-0 depth-feature annotation CSVs."""

    _require_license(accept_license)
    output = Path(root) / "annotations"
    results = []
    for name in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        results.append(
            download_file(
                f"{URFD_BASE_URL}/{name}",
                output / name,
                retries=retries,
                session=session,
            )
        )
    return results[0], results[1]


def download_urfd(
    root: str | Path,
    *,
    accept_license: bool,
    selection: Literal["smoke", "limit", "full"] = "smoke",
    limit: int | None = None,
    profile: Literal["preview", "rgb"] | None = None,
    extract: bool = True,
    keep_archives: bool = True,
    retries: int = 4,
    session: requests.Session | None = None,
) -> UrfdDownloadReport:
    """Download official URFD camera-0 RGB sequences and feature annotations."""

    _require_license(accept_license)
    resolved_profile = profile or ("preview" if selection == "smoke" else "rgb")
    if resolved_profile not in {"preview", "rgb"}:
        raise ValueError("profile must be 'preview' or 'rgb'")
    destination = Path(root)
    archives = destination / "archives"
    annotations = destination / "annotations"
    rgb = destination / "rgb"
    videos = destination / "videos"
    sequence_ids = select_urfd_sequences(selection, limit=limit)
    downloads: list[DownloadResult] = []
    extracted: list[Path] = []

    for asset in official_urfd_assets(
        selection, limit=limit, profile=resolved_profile
    ):
        if asset.kind == "features":
            target = annotations / asset.name
        elif asset.kind == "preview":
            target = videos / asset.name
        else:
            target = archives / asset.name
        result = download_file(asset.url, target, retries=retries, session=session)
        downloads.append(result)
        if asset.kind == "rgb" and extract:
            assert asset.sequence_id is not None
            sequence_output = rgb / asset.sequence_id
            safe_extract_zip(target, sequence_output)
            extracted.append(sequence_output)
            if not keep_archives:
                target.unlink(missing_ok=True)

    return UrfdDownloadReport(
        root=destination,
        selection=selection,
        profile=resolved_profile,
        sequence_ids=sequence_ids,
        downloads=tuple(downloads),
        extracted_directories=tuple(extracted),
    )


def parse_urfd_feature_csv(path: str | Path) -> pd.DataFrame:
    """Parse and strictly validate the official headerless 11-column CSV.

    Column names and ordering mirror the official dataset page exactly. The
    source label remains unchanged as ``-1/0/1``; use
    :func:`map_posture_labels` for the model's ``0/1/2`` label space.
    """

    source = Path(path)
    rows: list[list[object]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != len(URFD_FEATURE_COLUMNS):
                raise ValueError(
                    f"{source}:{line_number}: expected 11 columns, found {len(row)}"
                )
            match = re.fullmatch(r"(fall|adl)-(\d{2})", row[0].strip().lower())
            if not match:
                raise ValueError(
                    f"{source}:{line_number}: invalid sequence name {row[0]!r}"
                )
            try:
                frame = int(row[1])
                label = int(row[2])
                values = [float(value) for value in row[3:]]
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: invalid numeric value") from exc
            if frame < 0:
                raise ValueError(f"{source}:{line_number}: frame must be non-negative")
            if label not in {-1, 0, 1}:
                raise ValueError(
                    f"{source}:{line_number}: posture label must be -1, 0, or 1"
                )
            if not np.isfinite(values).all():
                raise ValueError(f"{source}:{line_number}: features must be finite")
            rows.append([row[0].strip().lower(), frame, label, *values])

    frame = pd.DataFrame(rows, columns=URFD_FEATURE_COLUMNS)
    if frame.empty:
        return frame.astype(
            {
                "sequence": "string",
                "frame": "int64",
                "label": "int64",
                **{column: "float64" for column in _FLOAT_COLUMNS},
            }
        )
    frame = frame.astype(
        {
            "sequence": "string",
            "frame": "int64",
            "label": "int64",
            **{column: "float64" for column in _FLOAT_COLUMNS},
        }
    )
    duplicates = frame.duplicated(["sequence", "frame"])
    if duplicates.any():
        duplicate = frame.loc[duplicates, ["sequence", "frame"]].iloc[0]
        raise ValueError(
            f"{source}: duplicate frame {duplicate['sequence']}:{duplicate['frame']}"
        )
    return frame


def load_urfd_features(annotation_root: str | Path) -> pd.DataFrame:
    root = Path(annotation_root)
    if root.is_dir() and (root / "annotations").is_dir():
        root = root / "annotations"
    falls = parse_urfd_feature_csv(root / "urfall-cam0-falls.csv")
    adls = parse_urfd_feature_csv(root / "urfall-cam0-adls.csv")
    return pd.concat((falls, adls), ignore_index=True)


def map_posture_labels(raw_labels: Sequence[int] | np.ndarray) -> np.ndarray:
    """Map official ``-1/0/1`` posture values to ``UPRIGHT/TRANSITION/LYING``."""

    raw = np.asarray(raw_labels, dtype=np.int64)
    if not np.isin(raw, [-1, 0, 1]).all():
        invalid = np.unique(raw[~np.isin(raw, [-1, 0, 1])]).tolist()
        raise ValueError(f"Unexpected URFD posture labels: {invalid}")
    mapped = np.empty_like(raw)
    mapped[raw == -1] = int(PostureLabel.UPRIGHT)
    mapped[raw == 0] = int(PostureLabel.TRANSITION)
    mapped[raw == 1] = int(PostureLabel.LYING)
    return mapped


def derive_annotations(
    feature_rows: pd.DataFrame,
    sequence_id: str,
    frame_indices: Sequence[int] | np.ndarray,
    *,
    post_impact_hold_frames: int = 20,
    unknown_posture_label: int = IGNORE_LABEL,
) -> DerivedAnnotations:
    """Align official posture labels and derive a conservative fall event target.

    For fall sequences, the derived event begins at the first official
    transition frame and ends ``post_impact_hold_frames`` after the first lying
    frame. ADL event labels are always zero, including deliberate transitions
    and lying. This distinction prevents annotation leakage from posture into
    the event target.
    """

    if post_impact_hold_frames < 0:
        raise ValueError("post_impact_hold_frames must be non-negative")
    normalized_id = sequence_id.lower()
    if not re.fullmatch(r"(fall|adl)-\d{2}", normalized_id):
        raise ValueError(f"Invalid URFD sequence id: {sequence_id!r}")

    indices = np.asarray(frame_indices, dtype=np.int64)
    if indices.ndim != 1:
        raise ValueError("frame_indices must be one-dimensional")
    selected = feature_rows.loc[feature_rows["sequence"].astype(str) == normalized_id]
    label_by_frame = dict(
        zip(
            selected["frame"].astype(int).tolist(),
            selected["label"].astype(int).tolist(),
            strict=True,
        )
    )
    posture = np.full(indices.shape, unknown_posture_label, dtype=np.int64)
    known_positions: list[int] = []
    known_raw: list[int] = []
    for output_index, frame_number in enumerate(indices.tolist()):
        raw_label = label_by_frame.get(frame_number)
        if raw_label is not None:
            known_positions.append(output_index)
            known_raw.append(raw_label)
    if known_positions:
        posture[np.asarray(known_positions)] = map_posture_labels(known_raw)

    event = np.zeros(indices.shape, dtype=np.int64)
    if normalized_id.startswith("fall-") and not selected.empty:
        transition_frames = selected.loc[selected["label"] == 0, "frame"].astype(int)
        if not transition_frames.empty:
            start = int(transition_frames.min())
            lying_frames = selected.loc[
                (selected["label"] == 1) & (selected["frame"] >= start), "frame"
            ].astype(int)
            if not lying_frames.empty:
                impact = int(lying_frames.min())
                end = impact + post_impact_hold_frames
                event[(indices >= start) & (indices <= end)] = 1

    return DerivedAnnotations(posture, event)


def _sequence_from_path(path: Path) -> str | None:
    for value in (path.name, *reversed(path.parts)):
        match = _SEQUENCE_RE.search(value)
        if match:
            return _canonical_sequence_id(match.group("kind"), int(match.group("number")))
    return None


def _frame_number(path: Path) -> int:
    numbers = re.findall(r"\d+", path.stem)
    return int(numbers[-1]) if numbers else -1


def discover_urfd_sequences(
    root: str | Path,
    *,
    fps: float | None = None,
    camera_id: str = "cam0",
    require_annotations: bool = True,
    prefer: Literal["auto", "preview", "rgb"] = "auto",
    source_profile: Literal["auto", "preview", "rgb"] | None = None,
) -> list[SequenceRecord]:
    """Discover extracted official RGB image sequences under *root*."""

    if source_profile is not None:
        if prefer != "auto" and prefer != source_profile:
            raise ValueError("prefer and source_profile select conflicting sources")
        prefer = source_profile
    if prefer not in {"auto", "preview", "rgb"}:
        raise ValueError("prefer must be 'auto', 'preview', or 'rgb'")
    if fps is not None and fps <= 0:
        raise ValueError("fps must be positive")
    dataset_root = Path(root)
    rgb_root = dataset_root / "rgb" if (dataset_root / "rgb").is_dir() else dataset_root
    groups: dict[str, list[Path]] = {}
    if prefer != "preview":
        for image in rgb_root.rglob("*"):
            if not image.is_file() or image.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            sequence_id = _sequence_from_path(image)
            if sequence_id is not None:
                groups.setdefault(sequence_id, []).append(image)

    annotation_root = (
        dataset_root / "annotations"
        if (dataset_root / "annotations").is_dir()
        else dataset_root
    )
    records: list[SequenceRecord] = []
    for sequence_id, images in sorted(groups.items()):
        images.sort(key=lambda item: (_frame_number(item), item.as_posix()))
        kind = "fall" if sequence_id.startswith("fall-") else "adl"
        annotation_name = (
            "urfall-cam0-falls.csv" if kind == "fall" else "urfall-cam0-adls.csv"
        )
        annotation = annotation_root / annotation_name
        if require_annotations and not annotation.is_file():
            raise FileNotFoundError(
                f"Missing official annotation file for {sequence_id}: {annotation}"
            )

        # Extraction puts every archive below rgb/<sequence_id>; use that stable
        # root even when the ZIP itself contains another nested directory.
        expected_root = rgb_root / sequence_id
        if expected_root.is_dir():
            sequence_path = expected_root
        else:
            common = Path(Path(*Path(images[0]).parts))
            for image in images[1:]:
                common = Path(*Path(common).parts[: _common_prefix_length(common, image)])
            sequence_path = common if common.is_dir() else common.parent

        records.append(
            SequenceRecord(
                dataset="urfd",
                sequence_id=sequence_id,
                group_id=sequence_id,
                camera_id=camera_id,
                path=sequence_path,
                num_frames=len(images),
                fps=float(fps or 30.0),
                sequence_label=kind,
                annotation_path=annotation if annotation.is_file() else None,
            )
        )

    if prefer != "rgb":
        video_root = (
            dataset_root / "videos"
            if (dataset_root / "videos").is_dir()
            else dataset_root
        )
        existing_ids = {record.sequence_id for record in records}
        for video in sorted(video_root.rglob("*.mp4")):
            sequence_id = _sequence_from_path(video)
            if sequence_id is None or sequence_id in existing_ids:
                continue
            try:
                import cv2
            except ImportError as exc:
                raise RuntimeError(
                    "OpenCV is required to discover URFD preview videos"
                ) from exc
            capture = cv2.VideoCapture(str(video))
            try:
                if not capture.isOpened():
                    raise ValueError(f"OpenCV could not open preview video: {video}")
                num_frames = int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
                detected_fps = float(capture.get(cv2.CAP_PROP_FPS))
            finally:
                capture.release()
            if num_frames <= 0:
                raise ValueError(f"Preview video has no frames: {video}")
            source_fps = float(fps or detected_fps or 30.0)
            kind = "fall" if sequence_id.startswith("fall-") else "adl"
            annotation_name = (
                "urfall-cam0-falls.csv"
                if kind == "fall"
                else "urfall-cam0-adls.csv"
            )
            annotation = annotation_root / annotation_name
            if require_annotations and not annotation.is_file():
                raise FileNotFoundError(
                    f"Missing official annotation file for {sequence_id}: {annotation}"
                )
            records.append(
                SequenceRecord(
                    dataset="urfd",
                    sequence_id=sequence_id,
                    group_id=sequence_id,
                    camera_id=camera_id,
                    path=video,
                    num_frames=num_frames,
                    fps=source_fps,
                    sequence_label=kind,
                    annotation_path=annotation if annotation.is_file() else None,
                )
            )
    records.sort(key=lambda record: record.sequence_id)
    return records


def _common_prefix_length(left: Path, right: Path) -> int:
    count = 0
    for left_part, right_part in zip(left.parts, right.parts, strict=False):
        if left_part.casefold() != right_part.casefold():
            break
        count += 1
    return count


def filter_feature_rows(
    feature_rows: pd.DataFrame, sequence_ids: Iterable[str]
) -> pd.DataFrame:
    selected = {sequence_id.lower() for sequence_id in sequence_ids}
    return feature_rows.loc[feature_rows["sequence"].astype(str).isin(selected)].copy()
