from __future__ import annotations

import csv
import hashlib
import os
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from fallguard.domain.types import SequenceRecord

SPLIT_NAMES = ("train", "validation", "test")
MANIFEST_COLUMNS = (
    "dataset",
    "sequence_id",
    "group_id",
    "camera_id",
    "path",
    "num_frames",
    "fps",
    "sequence_label",
    "annotation_path",
    "split",
)


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    dataset: str
    sequence_id: str
    group_id: str
    camera_id: str
    path: Path
    num_frames: int
    fps: float
    sequence_label: str
    annotation_path: Path | None
    split: str

    @classmethod
    def from_record(cls, record: SequenceRecord, split: str) -> ManifestEntry:
        if split not in SPLIT_NAMES:
            raise ValueError(f"Invalid split: {split!r}")
        return cls(
            dataset=record.dataset,
            sequence_id=record.sequence_id,
            group_id=record.group_id,
            camera_id=record.camera_id,
            path=record.path,
            num_frames=record.num_frames,
            fps=record.fps,
            sequence_label=record.sequence_label,
            annotation_path=record.annotation_path,
            split=split,
        )

    def to_record(self) -> SequenceRecord:
        return SequenceRecord(
            dataset=self.dataset,
            sequence_id=self.sequence_id,
            group_id=self.group_id,
            camera_id=self.camera_id,
            path=self.path,
            num_frames=self.num_frames,
            fps=self.fps,
            sequence_label=self.sequence_label,
            annotation_path=self.annotation_path,
        )


def _validate_ratios(ratios: Mapping[str, float]) -> dict[str, float]:
    if set(ratios) != set(SPLIT_NAMES):
        raise ValueError(f"ratios must have exactly these keys: {SPLIT_NAMES}")
    parsed = {name: float(ratios[name]) for name in SPLIT_NAMES}
    if any(value < 0 for value in parsed.values()):
        raise ValueError("split ratios cannot be negative")
    if abs(sum(parsed.values()) - 1.0) > 1e-8:
        raise ValueError("split ratios must sum to 1.0")
    if parsed["train"] <= 0:
        raise ValueError("the train ratio must be positive")
    return parsed


def _allocation_counts(count: int, ratios: Mapping[str, float]) -> dict[str, int]:
    raw = {name: count * ratios[name] for name in SPLIT_NAMES}
    allocation = {name: int(raw[name]) for name in SPLIT_NAMES}
    remaining = count - sum(allocation.values())
    order = sorted(
        SPLIT_NAMES,
        key=lambda name: (raw[name] - allocation[name], ratios[name], name),
        reverse=True,
    )
    for name in order[:remaining]:
        allocation[name] += 1

    # When a class has enough groups, keep each requested split represented.
    requested = [name for name in SPLIT_NAMES if ratios[name] > 0]
    if count >= len(requested):
        for missing in (name for name in requested if allocation[name] == 0):
            donor = max(
                (name for name in requested if allocation[name] > 1),
                key=lambda name: allocation[name],
                default=None,
            )
            if donor is not None:
                allocation[donor] -= 1
                allocation[missing] += 1
    return allocation


def assign_group_splits(
    records: Sequence[SequenceRecord],
    *,
    ratios: Mapping[str, float] | None = None,
    seed: int = 42,
    stratify: bool = True,
) -> dict[str, str]:
    """Assign whole ``group_id`` values to one deterministic split.

    Multiple cameras or derived records sharing a group are never separated.
    Stratification is performed by sequence label when labels are consistent
    within a group.
    """

    parsed_ratios = _validate_ratios(
        ratios or {"train": 0.70, "validation": 0.15, "test": 0.15}
    )
    groups: dict[str, list[SequenceRecord]] = defaultdict(list)
    for record in records:
        if not record.group_id:
            raise ValueError(f"Record {record.sequence_id!r} has an empty group_id")
        groups[record.group_id].append(record)
    if not groups:
        return {}

    grouped_by_stratum: dict[str, list[str]] = defaultdict(list)
    for group_id, group_records in groups.items():
        labels = {record.sequence_label for record in group_records}
        if len(labels) != 1:
            raise ValueError(
                f"Group {group_id!r} has inconsistent sequence labels: {sorted(labels)}"
            )
        stratum = next(iter(labels)) if stratify else "__all__"
        grouped_by_stratum[stratum].append(group_id)

    assignments: dict[str, str] = {}
    for stratum in sorted(grouped_by_stratum):
        group_ids = sorted(grouped_by_stratum[stratum])
        # A stratum-specific digest prevents Python hash randomization while
        # keeping the result stable across platforms and process launches.
        digest = hashlib.sha256(f"{seed}:{stratum}".encode()).digest()
        stratum_seed = int.from_bytes(digest[:8], "big")
        random.Random(stratum_seed).shuffle(group_ids)
        counts = _allocation_counts(len(group_ids), parsed_ratios)
        cursor = 0
        for split in SPLIT_NAMES:
            for group_id in group_ids[cursor : cursor + counts[split]]:
                assignments[group_id] = split
            cursor += counts[split]
    assert len(assignments) == len(groups)
    return assignments


def assert_no_group_leakage(entries: Iterable[ManifestEntry]) -> None:
    observed: dict[str, str] = {}
    for entry in entries:
        existing = observed.setdefault(entry.group_id, entry.split)
        if existing != entry.split:
            raise ValueError(
                f"Group leakage: {entry.group_id!r} occurs in {existing!r} and "
                f"{entry.split!r}"
            )


def build_manifest(
    records: Sequence[SequenceRecord],
    *,
    ratios: Mapping[str, float] | None = None,
    seed: int = 42,
    stratify: bool = True,
) -> list[ManifestEntry]:
    assignments = assign_group_splits(
        records, ratios=ratios, seed=seed, stratify=stratify
    )
    entries = [
        ManifestEntry.from_record(record, assignments[record.group_id])
        for record in records
    ]
    entries.sort(key=lambda item: (item.split, item.sequence_id, item.camera_id))
    assert_no_group_leakage(entries)
    return entries


def _display_path(path: Path | None, relative_to: Path | None) -> str:
    if path is None:
        return ""
    if relative_to is not None:
        path = Path(os.path.relpath(path.resolve(), start=relative_to.resolve()))
    return path.as_posix()


def write_manifest(
    entries: Sequence[ManifestEntry],
    path: str | Path,
    *,
    relative_to: str | Path | None = None,
) -> Path:
    assert_no_group_leakage(entries)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = Path(relative_to) if relative_to is not None else None
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "dataset": entry.dataset,
                    "sequence_id": entry.sequence_id,
                    "group_id": entry.group_id,
                    "camera_id": entry.camera_id,
                    "path": _display_path(entry.path, base),
                    "num_frames": entry.num_frames,
                    "fps": f"{entry.fps:.8g}",
                    "sequence_label": entry.sequence_label,
                    "annotation_path": _display_path(entry.annotation_path, base),
                    "split": entry.split,
                }
            )
    temporary.replace(destination)
    return destination


def read_manifest(
    path: str | Path,
    *,
    resolve_from: str | Path | None = None,
) -> list[ManifestEntry]:
    source = Path(path)
    base = Path(resolve_from) if resolve_from is not None else None
    entries: list[ManifestEntry] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != MANIFEST_COLUMNS:
            raise ValueError(
                f"Unexpected manifest header. Expected {MANIFEST_COLUMNS}, "
                f"found {reader.fieldnames}"
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                record_path = Path(row["path"])
                annotation_value = row["annotation_path"].strip()
                annotation_path = Path(annotation_value) if annotation_value else None
                if base is not None:
                    if not record_path.is_absolute():
                        record_path = base / record_path
                    if annotation_path is not None and not annotation_path.is_absolute():
                        annotation_path = base / annotation_path
                entry = ManifestEntry(
                    dataset=row["dataset"],
                    sequence_id=row["sequence_id"],
                    group_id=row["group_id"],
                    camera_id=row["camera_id"],
                    path=record_path,
                    num_frames=int(row["num_frames"]),
                    fps=float(row["fps"]),
                    sequence_label=row["sequence_label"],
                    annotation_path=annotation_path,
                    split=row["split"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{source}:{line_number}: invalid manifest row") from exc
            if entry.split not in SPLIT_NAMES:
                raise ValueError(
                    f"{source}:{line_number}: invalid split {entry.split!r}"
                )
            if entry.num_frames < 0 or entry.fps <= 0:
                raise ValueError(
                    f"{source}:{line_number}: num_frames/fps must be positive"
                )
            entries.append(entry)
    assert_no_group_leakage(entries)
    return entries
