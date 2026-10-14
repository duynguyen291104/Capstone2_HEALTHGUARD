from __future__ import annotations

from pathlib import Path

import numpy as np


def save_snapshot(
    frame_bgr: np.ndarray,
    destination: str | Path,
    *,
    jpeg_quality: int = 90,
) -> Path:
    """Atomically save a BGR frame as JPEG evidence."""

    import cv2

    frame = np.asarray(frame_bgr)
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("snapshot frame must have shape [height, width, 3]")
    if frame.dtype != np.uint8:
        raise ValueError("snapshot frame must use uint8 pixels")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100")

    output = Path(destination)
    if output.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("snapshot destination must use .jpg or .jpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(
        ".jpg",
        np.ascontiguousarray(frame),
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
    )
    if not ok:
        raise RuntimeError("OpenCV could not encode the evidence snapshot")
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(output)
    return output


def extract_video_snapshot(
    video_path: str | Path,
    destination: str | Path,
    *,
    timestamp_ms: int,
    jpeg_quality: int = 90,
) -> Path:
    """Extract an approximate event frame from an annotated video."""

    import cv2

    source = Path(video_path)
    if not source.is_file():
        raise FileNotFoundError(f"video evidence source not found: {source}")
    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must be non-negative")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        raise ValueError(f"OpenCV could not open video evidence: {source}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not np.isfinite(fps) or fps <= 0:
            fps = 20.0
        frame_index = max(0, int(round(timestamp_ms * fps / 1000.0)))
        if total_frames > 0:
            frame_index = min(frame_index, total_frames - 1)
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError(
                f"could not read evidence frame {frame_index} from {source}"
            )
    finally:
        capture.release()
    return save_snapshot(frame, destination, jpeg_quality=jpeg_quality)
