from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import requests

from fallguard.data.download import UnsafeArchiveError, download_file, safe_extract_zip
from fallguard.data.manifest import (
    ManifestEntry,
    assert_no_group_leakage,
    build_manifest,
    read_manifest,
    write_manifest,
)
from fallguard.data.synthetic import generate_synthetic_dataset
from fallguard.data.urfd import (
    ANNOTATION_PROVENANCE,
    LicenseNotAccepted,
    derive_annotations,
    download_urfd,
    map_posture_labels,
    parse_urfd_feature_csv,
    select_urfd_sequences,
)
from fallguard.domain.enums import PostureLabel
from fallguard.domain.types import SequenceRecord
from fallguard.pose.prepare import validate_pose_npz


class _MemorySession:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.range_headers: list[str | None] = []

    def head(self, *_args: object, **_kwargs: object) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Length"] = str(len(self.content))
        response.url = "https://example.test/asset"
        return response

    def get(self, *_args: object, **kwargs: object) -> requests.Response:
        header = kwargs.get("headers", {}).get("Range")  # type: ignore[union-attr]
        self.range_headers.append(header)
        start = int(header.removeprefix("bytes=").removesuffix("-")) if header else 0
        response = requests.Response()
        response.status_code = 206 if header else 200
        body = self.content[start:]
        response.headers["Content-Length"] = str(len(body))
        if header:
            response.headers["Content-Range"] = (
                f"bytes {start}-{len(self.content) - 1}/{len(self.content)}"
            )
        response.raw = io.BytesIO(body)
        response.url = "https://example.test/asset"
        return response


def _feature_line(sequence: str, frame: int, label: int) -> str:
    return f"{sequence},{frame},{label},1,2,0.5,4,0.9,1700,800,0.1\n"


def test_download_file_resumes_and_validates_content_length(tmp_path: Path) -> None:
    content = b"0123456789" * 20
    target = tmp_path / "asset.bin"
    target.with_name("asset.bin.part").write_bytes(content[:37])
    session = _MemorySession(content)

    result = download_file(
        "https://example.test/asset",
        target,
        session=session,  # type: ignore[arg-type]
        retries=1,
        chunk_size=13,
    )

    assert target.read_bytes() == content
    assert result.resumed_from == 37
    assert session.range_headers == ["bytes=37-"]


def test_safe_extract_rejects_traversal_and_extracts_valid_zip(tmp_path: Path) -> None:
    good_zip = tmp_path / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as archive:
        archive.writestr("frames/frame-001.png", b"png")
    destination = safe_extract_zip(good_zip, tmp_path / "good")
    assert (destination / "frames" / "frame-001.png").read_bytes() == b"png"
    assert safe_extract_zip(good_zip, destination) == destination

    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../outside.txt", b"bad")
    try:
        safe_extract_zip(bad_zip, tmp_path / "bad")
    except UnsafeArchiveError:
        pass
    else:
        raise AssertionError("path-traversal ZIP was not rejected")
    assert not (tmp_path / "outside.txt").exists()


def test_urfd_csv_mapping_and_event_derivation_keep_adl_negative(
    tmp_path: Path,
) -> None:
    source = tmp_path / "features.csv"
    source.write_text(
        "".join(
            [
                _feature_line("fall-01", 1, -1),
                _feature_line("fall-01", 2, 0),
                _feature_line("fall-01", 3, 1),
                _feature_line("adl-10", 1, -1),
                _feature_line("adl-10", 2, 0),
                _feature_line("adl-10", 3, 1),
            ]
        ),
        encoding="utf-8",
    )
    rows = parse_urfd_feature_csv(source)
    assert list(rows.columns) == [
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
    ]
    mapped = map_posture_labels(np.asarray([-1, 0, 1]))
    assert mapped.tolist() == [
        int(PostureLabel.UPRIGHT),
        int(PostureLabel.TRANSITION),
        int(PostureLabel.LYING),
    ]

    frames = np.arange(1, 7)
    fall = derive_annotations(rows, "fall-01", frames, post_impact_hold_frames=1)
    adl = derive_annotations(rows, "adl-10", frames, post_impact_hold_frames=10)
    assert fall.posture_labels[:3].tolist() == [0, 1, 2]
    assert fall.event_labels.tolist() == [0, 1, 1, 1, 0, 0]
    assert adl.posture_labels[:3].tolist() == [0, 1, 2]
    assert adl.event_labels.tolist() == [0, 0, 0, 0, 0, 0]
    assert fall.provenance == ANNOTATION_PROVENANCE


def test_license_gate_and_subset_selection(tmp_path: Path) -> None:
    assert select_urfd_sequences("smoke") == ("fall-01", "adl-01")
    limited = select_urfd_sequences("limit", limit=5)
    assert limited == ("fall-01", "adl-01", "fall-02", "adl-02", "fall-03")
    try:
        download_urfd(tmp_path, accept_license=False)
    except LicenseNotAccepted:
        pass
    else:
        raise AssertionError("download proceeded without explicit license acceptance")


def test_group_manifest_never_leaks_across_splits(tmp_path: Path) -> None:
    records = []
    for index in range(12):
        group = f"group-{index:02d}"
        label = "fall" if index % 2 == 0 else "adl"
        for camera in ("cam0", "cam1"):
            records.append(
                SequenceRecord(
                    dataset="test",
                    sequence_id=f"{group}-{camera}",
                    group_id=group,
                    camera_id=camera,
                    path=tmp_path / group / camera,
                    num_frames=10,
                    fps=20,
                    sequence_label=label,
                )
            )
    entries = build_manifest(records, seed=7)
    assert_no_group_leakage(entries)
    path = write_manifest(entries, tmp_path / "manifest.csv", relative_to=tmp_path)
    loaded = read_manifest(path, resolve_from=tmp_path)
    assert [(item.group_id, item.split) for item in loaded] == [
        (item.group_id, item.split) for item in entries
    ]
    assert {entry.split for entry in entries} == {"train", "validation", "test"}


def test_manifest_paths_can_reach_sibling_data_directories(tmp_path: Path) -> None:
    media_path = tmp_path / "raw" / "sequence.mp4"
    annotation_path = tmp_path / "raw" / "labels.csv"
    entry = ManifestEntry(
        dataset="test",
        sequence_id="sequence",
        group_id="sequence",
        camera_id="cam0",
        path=media_path,
        num_frames=10,
        fps=20,
        sequence_label="adl",
        annotation_path=annotation_path,
        split="train",
    )
    manifest_dir = tmp_path / "manifests"
    manifest_path = write_manifest(
        [entry],
        manifest_dir / "sequences.csv",
        relative_to=manifest_dir,
    )

    contents = manifest_path.read_text(encoding="utf-8")
    assert "../raw/sequence.mp4" in contents
    loaded = read_manifest(manifest_path, resolve_from=manifest_dir)
    assert loaded[0].path.resolve() == media_path.resolve()
    assert loaded[0].annotation_path is not None
    assert loaded[0].annotation_path.resolve() == annotation_path.resolve()


def test_synthetic_dataset_is_deterministic_and_contract_valid(tmp_path: Path) -> None:
    first = generate_synthetic_dataset(tmp_path / "one", seed=123)
    second = generate_synthetic_dataset(tmp_path / "two", seed=123)
    assert first.window_report is not None
    assert {item.split for item in first.window_report.files} == {
        "train",
        "validation",
        "test",
    }
    for item in first.window_report.files:
        with np.load(item.path, allow_pickle=False) as windows:
            assert windows["features"].shape[1:] == (40, 239)
            assert len(windows["features"]) == item.num_windows
            assert np.isin(windows["posture_targets"], [0, 1, 2]).all()
            assert np.isin(windows["event_targets"], [0, 1]).all()
    first_by_id = {entry.sequence_id: entry for entry in first.entries}
    second_by_id = {entry.sequence_id: entry for entry in second.entries}
    assert first_by_id.keys() == second_by_id.keys()

    for sequence_id in first_by_id:
        left = first_by_id[sequence_id]
        right = second_by_id[sequence_id]
        with np.load(left.path, allow_pickle=False) as first_npz, np.load(
            right.path, allow_pickle=False
        ) as second_npz:
            for key in ("landmarks", "valid", "posture_labels", "event_labels"):
                assert np.array_equal(first_npz[key], second_npz[key])
            if left.sequence_label == "adl":
                assert not first_npz["event_labels"].any()
        info = validate_pose_npz(left.path, expected_sequence_id=sequence_id)
        assert info.provenance == "synthetic_v1"
