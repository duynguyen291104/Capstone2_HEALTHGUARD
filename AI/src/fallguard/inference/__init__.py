"""Causal inference building blocks for FallGuard."""

from fallguard.inference.buffer import CausalSequenceBuffer, SequenceBuffer
from fallguard.inference.classifier import (
    ClassifierProtocol,
    DummyClassifier,
    TemporalClassifier,
    TorchTemporalClassifier,
)
from fallguard.inference.evidence import extract_video_snapshot, save_snapshot
from fallguard.inference.pipeline import (
    FrameInference,
    InferencePipeline,
    VideoInferenceResult,
    infer_image_sequence,
    infer_video,
    pose_window_to_features,
)
from fallguard.inference.pose import MediaPipePoseEstimator, NullPoseEstimator, PoseEstimator
from fallguard.inference.state_machine import EventStateMachine, FallStateMachine

__all__ = [
    "CausalSequenceBuffer",
    "ClassifierProtocol",
    "DummyClassifier",
    "EventStateMachine",
    "extract_video_snapshot",
    "FallStateMachine",
    "FrameInference",
    "InferencePipeline",
    "MediaPipePoseEstimator",
    "NullPoseEstimator",
    "PoseEstimator",
    "SequenceBuffer",
    "TemporalClassifier",
    "TorchTemporalClassifier",
    "VideoInferenceResult",
    "infer_image_sequence",
    "infer_video",
    "pose_window_to_features",
    "save_snapshot",
]
