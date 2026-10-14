from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from fallguard.domain import EventUpdate, PoseFrame, Prediction
from fallguard.inference.buffer import CausalSequenceBuffer
from fallguard.inference.classifier import DummyClassifier, TemporalClassifier
from fallguard.inference.pose import NullPoseEstimator, PoseEstimator
from fallguard.inference.state_machine import FallStateMachine

POSE_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 7),
    (0, 4),
    (4, 5),
    (5, 6),
    (6, 8),
    (9, 10),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (24, 26),
    (26, 28),
)


@dataclass(slots=True)
class FrameInference:
    frame_index: int
    timestamp_ms: int
    pose: PoseFrame | None = None
    prediction: Prediction | None = None
    event: EventUpdate | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "pose_detected": self.pose is not None,
        }
        if self.prediction is not None:
            result["prediction"] = prediction_to_dict(self.prediction)
        if self.event is not None:
            result["event"] = event_to_dict(self.event)
        return result


@dataclass(slots=True)
class VideoInferenceResult:
    source: str
    output_video: str | None
    events_json: str | None
    frame_count: int
    pose_frame_count: int
    prediction_count: int
    duration_seconds: float
    processing_fps: float
    sampled_frame_count: int = 0
    source_fps: float = 0.0
    target_fps: float | None = None
    crop_right_half: bool = False
    frame_transform_applied: bool = False
    timeline: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    bounded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InferencePipeline:
    """Composable image/video inference with causal buffering."""

    def __init__(
        self,
        classifier: TemporalClassifier | None = None,
        pose_estimator: PoseEstimator | None = None,
        state_machine: FallStateMachine | None = None,
        *,
        window_size: int = 40,
        stride: int = 5,
        window_adapter: Callable[[Sequence[PoseFrame]], Any] | None = None,
    ) -> None:
        self.classifier = classifier or DummyClassifier()
        self.pose_estimator = pose_estimator or NullPoseEstimator()
        self.state_machine = state_machine or FallStateMachine()
        self.buffer = CausalSequenceBuffer(window_size=window_size, stride=stride)
        self.window_adapter = window_adapter
        self.latest_pose: PoseFrame | None = None
        self.latest_prediction: Prediction | None = None
        self.latest_event: EventUpdate | None = None

    def reset(self) -> None:
        self.buffer.clear()
        self.state_machine.reset()
        self.latest_pose = None
        self.latest_prediction = None
        self.latest_event = None
        reset_estimator = getattr(self.pose_estimator, "reset", None)
        if callable(reset_estimator):
            reset_estimator()

    def process_pose(self, pose: PoseFrame) -> FrameInference:
        self.latest_pose = pose
        due = self.buffer.append(pose)
        prediction: Prediction | None = None
        event: EventUpdate | None = None
        if due:
            window: Any = self.buffer.window()
            if self.window_adapter is not None:
                window = self.window_adapter(window)
            prediction = self.classifier.predict(window)
            if prediction.timestamp_ms != pose.timestamp_ms:
                prediction = Prediction(
                    posture=prediction.posture,
                    posture_probabilities=prediction.posture_probabilities,
                    fall_probability=prediction.fall_probability,
                    timestamp_ms=pose.timestamp_ms,
                    model_version=prediction.model_version,
                    latency_ms=prediction.latency_ms,
                    evidence=prediction.evidence,
                )
            event = self.state_machine.update(prediction, pose)
            self.latest_prediction = prediction
            self.latest_event = event
        return FrameInference(
            frame_index=pose.frame_index,
            timestamp_ms=pose.timestamp_ms,
            pose=pose,
            prediction=prediction,
            event=event,
        )

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        timestamp_ms: int,
    ) -> FrameInference:
        pose = self.pose_estimator.process(frame_bgr, frame_index, timestamp_ms)
        if pose is None:
            missing_pose = PoseFrame(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                landmarks=np.zeros((33, 4), dtype=np.float32),
                valid=np.zeros(33, dtype=bool),
                bbox_xyxy=None,
            )
            buffered = self.process_pose(missing_pose)
            self.latest_pose = None
            return FrameInference(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                pose=None,
                prediction=buffered.prediction,
                event=buffered.event,
            )
        return self.process_pose(pose)

    def predict_available(self) -> FrameInference | None:
        """Force one prediction for a partial final window.

        This is intended for bounded request/response inference. Streaming paths
        should wait for ``window_size`` frames and use :meth:`process_pose`.
        """

        frames = self.buffer.available()
        if not frames:
            return None
        window: Any = frames
        if self.window_adapter is not None:
            window = self.window_adapter(window)
        prediction = self.classifier.predict(window)
        final = frames[-1]
        if prediction.timestamp_ms != final.timestamp_ms:
            prediction = Prediction(
                posture=prediction.posture,
                posture_probabilities=prediction.posture_probabilities,
                fall_probability=prediction.fall_probability,
                timestamp_ms=final.timestamp_ms,
                model_version=prediction.model_version,
                latency_ms=prediction.latency_ms,
                evidence=prediction.evidence,
            )
        event = self.state_machine.update(prediction, final)
        self.latest_prediction = prediction
        self.latest_event = event
        return FrameInference(
            frame_index=final.frame_index,
            timestamp_ms=final.timestamp_ms,
            pose=final,
            prediction=prediction,
            event=event,
        )

    def run_video(
        self,
        input_path: str | Path,
        *,
        output_video: str | Path | None = None,
        events_json: str | Path | None = None,
        target_fps: float | None = 20.0,
        crop_right_half: bool = False,
        frame_transform: Callable[[np.ndarray], np.ndarray] | None = None,
        max_duration_seconds: float | None = 300.0,
        max_frames: int | None = None,
        reset: bool = True,
    ) -> VideoInferenceResult:
        import cv2

        source = Path(input_path)
        if not source.is_file():
            raise FileNotFoundError(f"Video not found: {source}")
        if max_duration_seconds is not None and max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        if target_fps is not None and target_fps <= 0:
            raise ValueError("target_fps must be positive")
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be at least 1")
        if reset:
            self.reset()

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"Unable to open video: {source}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not np.isfinite(fps) or fps <= 0:
            fps = 20.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if (
            max_duration_seconds is not None
            and total_frames > 0
            and total_frames / fps > max_duration_seconds
        ):
            capture.release()
            raise ValueError(
                f"Video duration exceeds the {max_duration_seconds:g} second processing limit"
            )

        writer = None
        output_frame_size: tuple[int, int] | None = None
        output_path = Path(output_video) if output_video is not None else None
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)

        timeline: list[dict[str, Any]] = []
        events_by_id: dict[str, dict[str, Any]] = {}
        frame_count = 0
        pose_count = 0
        prediction_count = 0
        sampled_frame_count = 0
        bounded = False
        effective_fps = min(target_fps, fps) if target_fps is not None else fps
        downsampling = effective_fps < fps
        next_sample_position = 0
        last_sample_timestamp_ms: int | None = None
        started = time.perf_counter()
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                source_timestamp_ms = int(round(frame_count * 1000.0 / fps))
                if (
                    max_duration_seconds is not None
                    and source_timestamp_ms / 1000.0 > max_duration_seconds
                ):
                    bounded = True
                    break
                if max_frames is not None and frame_count >= max_frames:
                    bounded = True
                    break
                processed_frame = _prepare_video_frame(
                    frame,
                    crop_right_half=crop_right_half,
                    frame_transform=frame_transform,
                )
                height, width = processed_frame.shape[:2]
                current_size = (width, height)
                if output_frame_size is None:
                    output_frame_size = current_size
                elif current_size != output_frame_size:
                    raise ValueError(
                        "frame_transform must return a consistent frame size; "
                        f"expected {output_frame_size}, got {current_size}"
                    )
                if writer is None and output_path is not None:
                    writer = cv2.VideoWriter(
                        str(output_path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps,
                        current_size,
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"Unable to create annotated video: {output_path}")

                should_sample = not downsampling or frame_count >= next_sample_position
                if should_sample:
                    timestamp_ms = int(
                        np.rint(sampled_frame_count * 1000.0 / effective_fps)
                    )
                    if (
                        last_sample_timestamp_ms is not None
                        and timestamp_ms <= last_sample_timestamp_ms
                    ):
                        timestamp_ms = last_sample_timestamp_ms + 1
                    frame_result = self.process_frame(
                        processed_frame,
                        frame_count,
                        timestamp_ms,
                    )
                    sampled_frame_count += 1
                    last_sample_timestamp_ms = timestamp_ms
                    if downsampling:
                        next_target_timestamp_ms = (
                            sampled_frame_count * 1000.0 / effective_fps
                        )
                        next_sample_position = int(
                            np.rint(next_target_timestamp_ms * fps / 1000.0)
                        )
                else:
                    frame_result = FrameInference(
                        frame_index=frame_count,
                        timestamp_ms=source_timestamp_ms,
                    )
                frame_count += 1
                if frame_result.pose is not None:
                    pose_count += 1
                if frame_result.prediction is not None:
                    prediction_count += 1
                    timeline.append(frame_result.to_dict())
                if frame_result.event is not None and frame_result.event.event_id is not None:
                    events_by_id[frame_result.event.event_id] = event_to_dict(frame_result.event)
                if writer is not None:
                    writer.write(self.annotate_frame(processed_frame, frame_result))
        finally:
            capture.release()
            if writer is not None:
                writer.release()

        elapsed = max(time.perf_counter() - started, 1e-9)
        result = VideoInferenceResult(
            source=str(source),
            output_video=str(output_path) if output_path is not None else None,
            events_json=str(events_json) if events_json is not None else None,
            frame_count=frame_count,
            pose_frame_count=pose_count,
            prediction_count=prediction_count,
            duration_seconds=frame_count / fps,
            processing_fps=frame_count / elapsed,
            sampled_frame_count=sampled_frame_count,
            source_fps=fps,
            target_fps=target_fps,
            crop_right_half=crop_right_half,
            frame_transform_applied=frame_transform is not None,
            timeline=timeline,
            events=list(events_by_id.values()),
            bounded=bounded,
        )
        if events_json is not None:
            json_path = Path(events_json)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return result

    def run_image_sequence(
        self,
        images: Iterable[str | Path | np.ndarray],
        *,
        fps: float = 20.0,
        output_dir: str | Path | None = None,
        events_json: str | Path | None = None,
        reset: bool = True,
    ) -> VideoInferenceResult:
        import cv2

        if fps <= 0:
            raise ValueError("fps must be positive")
        if reset:
            self.reset()
        destination = Path(output_dir) if output_dir is not None else None
        if destination is not None:
            destination.mkdir(parents=True, exist_ok=True)

        started = time.perf_counter()
        timeline: list[dict[str, Any]] = []
        events_by_id: dict[str, dict[str, Any]] = {}
        pose_count = 0
        frame_count = 0
        prediction_count = 0
        for index, image in enumerate(images):
            if isinstance(image, (str, Path)):
                frame = cv2.imread(str(image))
                if frame is None:
                    raise ValueError(f"Unable to read image: {image}")
            else:
                frame = np.asarray(image)
                if frame.ndim != 3 or frame.shape[2] != 3:
                    raise ValueError("Image arrays must have shape [height, width, 3]")
            timestamp_ms = int(round(index * 1000.0 / fps))
            frame_result = self.process_frame(frame, index, timestamp_ms)
            frame_count += 1
            if frame_result.pose is not None:
                pose_count += 1
            if frame_result.prediction is not None:
                prediction_count += 1
                timeline.append(frame_result.to_dict())
            if frame_result.event is not None and frame_result.event.event_id is not None:
                events_by_id[frame_result.event.event_id] = event_to_dict(frame_result.event)
            if destination is not None:
                cv2.imwrite(
                    str(destination / f"frame_{index:06d}.jpg"),
                    self.annotate_frame(frame, frame_result),
                )

        elapsed = max(time.perf_counter() - started, 1e-9)
        result = VideoInferenceResult(
            source="image-sequence",
            output_video=str(destination) if destination is not None else None,
            events_json=str(events_json) if events_json is not None else None,
            frame_count=frame_count,
            pose_frame_count=pose_count,
            prediction_count=prediction_count,
            duration_seconds=frame_count / fps,
            processing_fps=frame_count / elapsed,
            sampled_frame_count=frame_count,
            source_fps=fps,
            target_fps=fps,
            timeline=timeline,
            events=list(events_by_id.values()),
        )
        if events_json is not None:
            json_path = Path(events_json)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return result

    run_images = run_image_sequence

    def annotate_frame(
        self,
        frame_bgr: np.ndarray,
        result: FrameInference | None = None,
    ) -> np.ndarray:
        import cv2

        canvas = frame_bgr.copy()
        pose = (
            result.pose
            if result is not None and result.pose is not None
            else self.latest_pose
        )
        if pose is not None:
            height, width = canvas.shape[:2]
            points = pose.landmarks[:, :2]
            for start, end in POSE_CONNECTIONS:
                if pose.valid[start] and pose.valid[end]:
                    start_xy = (
                        int(np.clip(points[start, 0], 0, 1) * width),
                        int(np.clip(points[start, 1], 0, 1) * height),
                    )
                    end_xy = (
                        int(np.clip(points[end, 0], 0, 1) * width),
                        int(np.clip(points[end, 1], 0, 1) * height),
                    )
                    cv2.line(canvas, start_xy, end_xy, (82, 224, 170), 2, cv2.LINE_AA)
            for index in np.flatnonzero(pose.valid):
                point = (
                    int(np.clip(points[index, 0], 0, 1) * width),
                    int(np.clip(points[index, 1], 0, 1) * height),
                )
                cv2.circle(canvas, point, 3, (255, 255, 255), -1, cv2.LINE_AA)

        prediction = (
            result.prediction
            if result is not None and result.prediction is not None
            else self.latest_prediction
        )
        event = result.event if result is not None and result.event is not None else self.latest_event
        state_text = event.state.value if event is not None else "WARMING_UP"
        fall_probability = prediction.fall_probability if prediction is not None else 0.0
        posture = prediction.posture.name if prediction is not None else "-"
        color = (68, 211, 122)
        if event is not None and event.risk_level.value == "HIGH":
            color = (72, 72, 239)
        elif event is not None and event.risk_level.value == "MEDIUM":
            color = (66, 183, 245)
        cv2.rectangle(canvas, (16, 16), (365, 105), (13, 20, 31), -1)
        cv2.putText(
            canvas,
            f"STATE  {state_text}",
            (30, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"Posture {posture}   Fall {fall_probability:.1%}",
            (30, 78),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (232, 238, 245),
            1,
            cv2.LINE_AA,
        )
        return canvas


def pose_window_to_features(
    window: Sequence[PoseFrame],
    *,
    max_interpolation_gap: int = 3,
    include_xyz: bool = True,
    include_visibility: bool = True,
    include_velocity: bool = True,
    include_geometry: bool = True,
) -> np.ndarray:
    """Apply the training feature recipe to an in-memory pose window."""

    if not window:
        raise ValueError("Pose window must contain at least one frame")
    from fallguard.features import build_frame_features

    landmarks = np.stack([frame.landmarks for frame in window]).astype(np.float32)
    valid = np.stack([frame.valid for frame in window]).astype(bool)
    return build_frame_features(
        landmarks,
        valid,
        max_interpolation_gap=max_interpolation_gap,
        include_xyz=include_xyz,
        include_visibility=include_visibility,
        include_velocity=include_velocity,
        include_geometry=include_geometry,
    ).values


def _prepare_video_frame(
    frame: np.ndarray,
    *,
    crop_right_half: bool,
    frame_transform: Callable[[np.ndarray], np.ndarray] | None,
) -> np.ndarray:
    prepared = np.asarray(frame)
    if crop_right_half:
        if prepared.shape[1] < 2:
            raise ValueError("Cannot crop the right half of a frame narrower than 2 pixels")
        prepared = prepared[:, prepared.shape[1] // 2 :]
    if frame_transform is not None:
        prepared = np.asarray(frame_transform(prepared))
    if prepared.ndim != 3 or prepared.shape[2] != 3:
        raise ValueError(
            "frame_transform must return a BGR image with shape [height, width, 3]"
        )
    if prepared.shape[0] < 1 or prepared.shape[1] < 1:
        raise ValueError("Processed video frames must have non-zero dimensions")
    if prepared.dtype != np.uint8:
        raise ValueError("frame_transform must return uint8 video frames")
    return np.ascontiguousarray(prepared)


def prediction_to_dict(prediction: Prediction) -> dict[str, Any]:
    return {
        "posture": prediction.posture.name,
        "posture_probabilities": {
            label.name: float(prediction.posture_probabilities[int(label)])
            for label in type(prediction.posture)
        },
        "confidence": prediction.confidence,
        "fall_probability": float(prediction.fall_probability),
        "timestamp_ms": int(prediction.timestamp_ms),
        "model_version": prediction.model_version,
        "latency_ms": float(prediction.latency_ms),
        "evidence": list(prediction.evidence),
    }


def event_to_dict(event: EventUpdate) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "state": event.state.value,
        "risk_level": event.risk_level.value,
        "timestamp_ms": int(event.timestamp_ms),
        "inactive_seconds": float(event.inactive_seconds),
        "self_recovery": bool(event.self_recovery),
        "evidence": list(event.evidence),
        "metadata": dict(event.metadata),
    }


def infer_video(
    input_path: str | Path,
    *,
    classifier: TemporalClassifier | None = None,
    pose_estimator: PoseEstimator | None = None,
    output_video: str | Path | None = None,
    events_json: str | Path | None = None,
    **kwargs: Any,
) -> VideoInferenceResult:
    pipeline = InferencePipeline(classifier=classifier, pose_estimator=pose_estimator)
    return pipeline.run_video(
        input_path,
        output_video=output_video,
        events_json=events_json,
        **kwargs,
    )


def infer_image_sequence(
    images: Iterable[str | Path | np.ndarray],
    *,
    classifier: TemporalClassifier | None = None,
    pose_estimator: PoseEstimator | None = None,
    fps: float = 20.0,
    output_dir: str | Path | None = None,
    events_json: str | Path | None = None,
) -> VideoInferenceResult:
    pipeline = InferencePipeline(classifier=classifier, pose_estimator=pose_estimator)
    return pipeline.run_image_sequence(
        images,
        fps=fps,
        output_dir=output_dir,
        events_json=events_json,
    )
