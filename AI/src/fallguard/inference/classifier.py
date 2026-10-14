from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

import numpy as np

from fallguard.domain import PoseFrame, PostureLabel, Prediction

Window = Sequence[PoseFrame] | np.ndarray


@runtime_checkable
class TemporalClassifier(Protocol):
    """Runtime contract implemented by dummy and trained classifiers."""

    model_version: str

    def predict(self, window: Window) -> Prediction:
        """Predict posture and event probability for the final window frame."""


ClassifierProtocol = TemporalClassifier


class DummyClassifier:
    """Conservative offline fallback that can never emit a fall alert."""

    model_version = "dummy-safe-v1"
    model_name = "Safe fallback"
    is_fallback = True

    def __init__(self, reason: str = "no trained checkpoint loaded") -> None:
        self.reason = reason

    def predict(self, window: Window) -> Prediction:
        timestamp_ms = _target_timestamp(window)
        return Prediction(
            posture=PostureLabel.UPRIGHT,
            posture_probabilities=(1.0, 0.0, 0.0),
            fall_probability=0.0,
            timestamp_ms=timestamp_ms,
            model_version=self.model_version,
            evidence=(f"Safe fallback classifier; {self.reason}",),
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.model_name,
            "version": self.model_version,
            "backend": "deterministic",
            "ready": True,
            "fallback": True,
            "fallback_reason": self.reason,
            "posture_classes": [label.name for label in PostureLabel],
        }


class TorchTemporalClassifier:
    """Thin adapter from a trained PyTorch multi-task model to the domain API.

    ``feature_adapter`` owns preprocessing and must return a ``[time, features]``
    NumPy array (or a tensor with the same shape). Keeping it injectable makes
    checkpoint inference use exactly the feature recipe recorded at training
    time instead of duplicating that recipe here.
    """

    is_fallback = False

    def __init__(
        self,
        model: Any,
        feature_adapter: Callable[[Window], Any],
        *,
        model_version: str = "unversioned",
        device: str = "cpu",
        model_name: str | None = None,
    ) -> None:
        if not callable(feature_adapter):
            raise TypeError("feature_adapter must be callable")
        self.model = model
        self.feature_adapter = feature_adapter
        self.model_version = model_version
        self.device = device
        self.model_name = model_name or type(model).__name__
        if hasattr(self.model, "to"):
            self.model.to(self.device)
        if hasattr(self.model, "eval"):
            self.model.eval()

    def predict(self, window: Window) -> Prediction:
        import torch

        features = self.feature_adapter(window)
        tensor = torch.as_tensor(features, dtype=torch.float32, device=self.device)
        if tensor.ndim != 2:
            raise ValueError(
                "feature_adapter must return [time, features], "
                f"got shape {tuple(tensor.shape)}"
            )

        started = __import__("time").perf_counter()
        with torch.inference_mode():
            output = self.model(tensor.unsqueeze(0))
            posture_logits, event_logits = _unpack_model_output(output)
            posture_probabilities = torch.softmax(posture_logits, dim=-1)[0]
            event_value = event_logits.reshape(-1)[0]
            fall_probability = torch.sigmoid(event_value)
        latency_ms = (__import__("time").perf_counter() - started) * 1000.0

        probabilities = tuple(float(value) for value in posture_probabilities.cpu().tolist())
        if len(probabilities) != 3:
            raise ValueError(f"Expected three posture probabilities, got {len(probabilities)}")
        posture = PostureLabel(int(np.argmax(probabilities)))
        return Prediction(
            posture=posture,
            posture_probabilities=probabilities,
            fall_probability=float(fall_probability.cpu().item()),
            timestamp_ms=_target_timestamp(window),
            model_version=self.model_version,
            latency_ms=latency_ms,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.model_name,
            "version": self.model_version,
            "backend": "pytorch",
            "device": self.device,
            "ready": True,
            "fallback": False,
            "posture_classes": [label.name for label in PostureLabel],
        }


def _target_timestamp(window: Window) -> int:
    if isinstance(window, np.ndarray):
        return 0
    if not window:
        raise ValueError("Classifier window must contain at least one frame")
    final = window[-1]
    if not isinstance(final, PoseFrame):
        raise TypeError("Sequence windows must contain PoseFrame values")
    return int(final.timestamp_ms)


def _unpack_model_output(output: Any) -> tuple[Any, Any]:
    if hasattr(output, "posture_logits") and hasattr(output, "event_logits"):
        return output.posture_logits, output.event_logits
    if isinstance(output, dict):
        event = output.get("event_logits", output.get("fall_logits"))
        if "posture_logits" in output and event is not None:
            return output["posture_logits"], event
    if isinstance(output, (tuple, list)) and len(output) == 2:
        return output[0], output[1]
    raise TypeError("Model must return posture_logits and event_logits")
