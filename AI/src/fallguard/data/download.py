from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import requests


class DownloadError(RuntimeError):
    """Raised when a remote asset cannot be downloaded and validated."""


class UnsafeArchiveError(ValueError):
    """Raised when a ZIP member could escape the requested extraction directory."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    url: str
    path: Path
    size_bytes: int
    resumed_from: int
    downloaded: bool


def _content_length(headers: requests.structures.CaseInsensitiveDict[str]) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DownloadError(f"Invalid Content-Length header: {value!r}") from exc
    if parsed < 0:
        raise DownloadError(f"Invalid negative Content-Length header: {value!r}")
    return parsed


def _total_from_content_range(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+|\*)", value.strip())
    if not match or match.group(1) == "*":
        return None
    return int(match.group(1))


def _remote_size(
    session: requests.Session,
    url: str,
    *,
    timeout: tuple[float, float],
) -> int | None:
    try:
        response = session.head(
            url,
            allow_redirects=True,
            timeout=timeout,
            headers={"Accept-Encoding": "identity"},
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return _content_length(response.headers)


def download_file(
    url: str,
    destination: str | Path,
    *,
    retries: int = 4,
    timeout: tuple[float, float] = (15.0, 90.0),
    chunk_size: int = 1024 * 1024,
    expected_size: int | None = None,
    session: requests.Session | None = None,
) -> DownloadResult:
    """Download *url* atomically, retaining ``.part`` data for HTTP range resume.

    Both the response Content-Length and the final remote size (when published)
    are checked. A server that ignores ``Range`` is handled by restarting the
    partial file instead of appending duplicate bytes.
    """

    if retries < 1:
        raise ValueError("retries must be at least 1")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    own_session = session is None
    client = session or requests.Session()
    resumed_from = 0

    try:
        remote_size = expected_size
        if remote_size is None:
            remote_size = _remote_size(client, url, timeout=timeout)

        if target.is_file():
            local_size = target.stat().st_size
            if remote_size is None or local_size == remote_size:
                return DownloadResult(url, target, local_size, local_size, False)
            if local_size < remote_size and not partial.exists():
                os.replace(target, partial)

        last_error: Exception | None = None
        for attempt in range(retries):
            offset = partial.stat().st_size if partial.exists() else 0
            resumed_from = max(resumed_from, offset)
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"

            try:
                with client.get(
                    url,
                    stream=True,
                    allow_redirects=True,
                    timeout=timeout,
                    headers=headers,
                ) as response:
                    if response.status_code == 416 and remote_size == offset:
                        os.replace(partial, target)
                        return DownloadResult(url, target, offset, offset, True)
                    response.raise_for_status()

                    append = bool(offset and response.status_code == 206)
                    if response.status_code == 206:
                        content_range = response.headers.get("Content-Range", "")
                        range_match = re.match(r"bytes\s+(\d+)-", content_range)
                        if not range_match or int(range_match.group(1)) != offset:
                            raise DownloadError(
                                f"Server returned an invalid Content-Range: {content_range!r}"
                            )
                    elif offset:
                        # Range was ignored. A clean restart prevents a corrupt append.
                        append = False
                        offset = 0

                    response_length = _content_length(response.headers)
                    response_total = _total_from_content_range(
                        response.headers.get("Content-Range")
                    )
                    if response_total is not None:
                        if remote_size is not None and response_total != remote_size:
                            raise DownloadError(
                                "Remote size changed while downloading "
                                f"({remote_size} -> {response_total})"
                            )
                        remote_size = response_total
                    elif remote_size is None and response_length is not None:
                        remote_size = offset + response_length

                    mode = "ab" if append else "wb"
                    bytes_received = 0
                    with partial.open(mode) as handle:
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            bytes_received += len(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())

                    if response_length is not None and bytes_received != response_length:
                        raise DownloadError(
                            f"Content-Length mismatch for {url}: "
                            f"received {bytes_received}, expected {response_length}"
                        )

                final_size = partial.stat().st_size
                if expected_size is not None and final_size != expected_size:
                    raise DownloadError(
                        f"Downloaded size mismatch for {url}: "
                        f"received {final_size}, expected {expected_size}"
                    )
                if remote_size is not None and final_size != remote_size:
                    raise DownloadError(
                        f"Downloaded size mismatch for {url}: "
                        f"received {final_size}, expected {remote_size}"
                    )

                os.replace(partial, target)
                return DownloadResult(url, target, final_size, resumed_from, True)
            except (OSError, requests.RequestException, DownloadError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(2**attempt, 8))

        raise DownloadError(f"Failed to download {url} after {retries} attempts") from last_error
    finally:
        if own_session:
            client.close()


def validate_zip_crc(path: str | Path) -> None:
    """Read every ZIP member and fail on a bad CRC or malformed archive."""

    archive = Path(path)
    try:
        with zipfile.ZipFile(archive) as handle:
            bad_member = handle.testzip()
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise DownloadError(f"Invalid ZIP archive: {archive}") from exc
    if bad_member is not None:
        raise DownloadError(f"ZIP CRC validation failed for member {bad_member!r}")


def _safe_member_path(destination: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafeArchiveError(f"Unsafe ZIP member path: {member_name!r}")
    # Reject Windows drive paths even when running on POSIX in tests.
    if pure.parts and re.match(r"^[A-Za-z]:", pure.parts[0]):
        raise UnsafeArchiveError(f"Unsafe ZIP member path: {member_name!r}")

    root = destination.resolve()
    candidate = (destination / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeArchiveError(f"ZIP member escapes destination: {member_name!r}") from exc
    return candidate


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def safe_extract_zip(
    archive_path: str | Path,
    destination: str | Path,
    *,
    marker_name: str = ".fallguard-extracted.json",
    max_uncompressed_bytes: int | None = None,
) -> Path:
    """CRC-check and safely extract a ZIP without path traversal or symlinks.

    A marker containing archive size and mtime makes repeated preparation
    idempotent. Extraction writes each file via a temporary sibling before an
    atomic replace, so interruption never leaves a half-written image.
    """

    archive = Path(archive_path)
    output = Path(destination)
    signature = {
        "archive": archive.name,
        "size": archive.stat().st_size,
        "mtime_ns": archive.stat().st_mtime_ns,
    }
    marker = output / marker_name
    if marker.is_file():
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == signature:
                return output
        except (OSError, ValueError, TypeError):
            pass

    validate_zip_crc(archive)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        total_size = sum(info.file_size for info in infos)
        if max_uncompressed_bytes is not None and total_size > max_uncompressed_bytes:
            raise UnsafeArchiveError(
                f"Archive expands to {total_size} bytes, over limit {max_uncompressed_bytes}"
            )

        validated: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in infos:
            target = _safe_member_path(output, info.filename)
            if _is_symlink(info):
                raise UnsafeArchiveError(f"Symlink ZIP member is not allowed: {info.filename!r}")
            validated.append((info, target))

        for info, target in validated:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            try:
                with os.fdopen(descriptor, "w+b") as temporary:
                    with handle.open(info) as source:
                        shutil.copyfileobj(source, temporary, length=1024 * 1024)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, target)
            finally:
                Path(temporary_name).unlink(missing_ok=True)

    marker_tmp = marker.with_suffix(f"{marker.suffix}.tmp")
    marker_tmp.write_text(json.dumps(signature, sort_keys=True), encoding="utf-8")
    os.replace(marker_tmp, marker)
    return output
