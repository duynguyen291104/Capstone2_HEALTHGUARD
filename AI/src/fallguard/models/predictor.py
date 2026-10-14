from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import Tensor, nn

from fallguard.domain.enums import PostureLabel
from fallguard.domain.types import Prediction
from fallguard.models.temporal import TemporalOutput, build_model


class TemporalPredictor:
    """Checkpoint-backed runtime adapter returning the domain Prediction type."""

    is_fallback = False

    def __init__(
        self,
        model: nn.Module,
        *,
        device: str | torch.device = "auto",
        model_version: str = "untrained",
    ) -> None:
        self.device = resolve_inference_device(device)
        self.model = model.to(self.device)
        self.model.eval()
        self.model_version = model_version
        self.model_name = type(model).__name__
        self.checkpoint_config: dict[str, Any] = {}
        self.checkpoint_path: Path | None = None

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path,
        *,
        device: str | torch.device = "auto",
        model_config: Any = None,
        input_size: int | None = None,
        strict: bool = True,
    ) -> TemporalPredictor:
        checkpoint_file = Path(checkpoint_path)
        if not checkpoint_file.is_file():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_file}")
        checkpoint = _load_torch_checkpoint(checkpoint_file, map_location="cpu")

        if _looks_like_state_dict(checkpoint):
            state_dict = checkpoint
            checkpoint_metadata: Mapping[str, Any] = {}
        elif isinstance(checkpoint, Mapping):
            state_dict = checkpoint.get("model_state_dict", checkpoint.get("state_dict"))
            if not isinstance(state_dict, Mapping):
                raise ValueError("Checkpoint does not contain a model state dictionary")
            checkpoint_metadata = checkpoint
        else:
            raise ValueError("Unsupported checkpoint format")

        specification = _checkpoint_model_spec(checkpoint_metadata)
        if model_config is not None:
            specification.update(_as_model_mapping(model_config))
        if input_size is not None:
            specification["input_size"] = int(input_size)
        if "input_size" not in specification:
            raise ValueError(
                "Checkpoint does not record input_size; pass input_size and model_config"
            )

        model = build_model(specification)
        model.load_state_dict(state_dict, strict=strict)
        model_version = str(
            checkpoint_metadata.get(
                "model_version",
                f"{model.architecture}:{checkpoint_file.stem}",
            )
        )
        predictor = cls(model, device=device, model_version=model_version)
        predictor.checkpoint_path = checkpoint_file
        stored_config = checkpoint_metadata.get("config")
        if isinstance(stored_config, Mapping):
            predictor.checkpoint_config = dict(stored_config)
        return predictor

    def predict(
        self,
        window: np.ndarray | Tensor,
        *,
        timestamp_ms: int = 0,
    ) -> Prediction:
        started_at = time.perf_counter()
        inputs = _prepare_window(window, expected_input_size=self.input_size)
        inputs = inputs.to(self.device)
        with torch.inference_mode():
            output = self.model(inputs)
            posture_logits, event_logits = unpack_temporal_output(output)
            posture_probabilities = torch.softmax(posture_logits, dim=-1)[0]
            fall_probability = torch.sigmoid(event_logits.reshape(-1))[0]

        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        probabilities = tuple(
            float(value) for value in posture_probabilities.detach().cpu().tolist()
        )
        if len(probabilities) != len(PostureLabel):
            raise ValueError(
                "Runtime Prediction schema requires exactly three posture classes, "
                f"but the model produced {len(probabilities)}"
            )
        posture = PostureLabel(int(np.argmax(probabilities)))
        return Prediction(
            posture=posture,
            posture_probabilities=probabilities,
            fall_probability=float(fall_probability.detach().cpu()),
            timestamp_ms=int(timestamp_ms),
            model_version=self.model_version,
            latency_ms=float(latency_ms),
        )

    def predict_batch(self, windows: np.ndarray | Tensor) -> tuple[Tensor, Tensor]:
        inputs = torch.as_tensor(windows, dtype=torch.float32)
        if inputs.ndim < 3:
            raise ValueError("Batch input must have shape [batch, time, ...features]")
        inputs = inputs.flatten(start_dim=2)
        if inputs.shape[-1] != self.input_size:
            raise ValueError(f"Expected {self.input_size} input features, got {inputs.shape[-1]}")
        if not torch.isfinite(inputs).all():
            raise ValueError("Input window contains NaN or infinite values")
        with torch.inference_mode():
            output = self.model(inputs.to(self.device))
            posture_logits, event_logits = unpack_temporal_output(output)
            return (
                torch.softmax(posture_logits, dim=-1).cpu(),
                torch.sigmoid(event_logits).cpu(),
            )

    @property
    def input_size(self) -> int:
        input_size = getattr(self.model, "input_size", None)
        if input_size is None:
            raise AttributeError("Wrapped model does not expose input_size")
        return int(input_size)

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.model_name,
            "version": self.model_version,
            "backend": "pytorch",
            "device": str(self.device),
            "ready": True,
            "fallback": False,
            "architecture": getattr(self.model, "architecture", type(self.model).__name__),
            "input_size": self.input_size,
            "posture_classes": getattr(self.model, "posture_classes", 3),
            "checkpoint": (str(self.checkpoint_path) if self.checkpoint_path is not None else None),
        }


# Runtime interface name from docs/architecture.md.
TemporalClassifier = TemporalPredictor


def load_predictor(
    checkpoint_path: str | Path,
    **kwargs: Any,
) -> TemporalPredictor:
    return TemporalPredictor.from_checkpoint(checkpoint_path, **kwargs)


def unpack_temporal_output(output: Any) -> tuple[Tensor, Tensor]:
    """Accept the project's output type plus conventional tuple/dict models."""

    if isinstance(output, TemporalOutput):
        return output.posture_logits, output.event_logits
    if isinstance(output, Mapping):
        posture = output.get("posture_logits")
        event = output.get("event_logits", output.get("fall_logits"))
        if isinstance(posture, Tensor) and isinstance(event, Tensor):
            return posture, event
    if isinstance(output, (tuple, list)) and len(output) == 2:
        posture, event = output
        if isinstance(posture, Tensor) and isinstance(event, Tensor):
            return posture, event
    if hasattr(output, "posture_logits") and (
        hasattr(output, "event_logits") or hasattr(output, "fall_logits")
    ):
        event = getattr(output, "event_logits", getattr(output, "fall_logits", None))
        return output.posture_logits, event
    raise TypeError("Model must return (posture_logits, event_logits) or equivalent named fields")


def resolve_inference_device(device: str | torch.device) -> torch.device:
    if isinstance(device, torch.device):
        requested = device
    else:
        normalized = str(device).lower()
        if normalized == "auto":
            normalized = "cuda" if torch.cuda.is_available() else "cpu"
        requested = torch.device(normalized)
    if requested.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return requested


def _prepare_window(window: np.ndarray | Tensor, expected_input_size: int) -> Tensor:
    values = torch.as_tensor(window, dtype=torch.float32)
    if values.ndim < 2:
        raise ValueError("Window input must have shape [time, ...features]")
    if values.ndim >= 3 and values.shape[0] == 1:
        flattened_single_frame_size = int(np.prod(values.shape[1:]))
        if flattened_single_frame_size != expected_input_size:
            # Accept an already-batched single sample. If all remaining
            # dimensions themselves form one feature vector (for example
            # [1, 33, 4]), the leading one is instead a one-frame time axis.
            values = values.squeeze(0)
    values = values.flatten(start_dim=1)
    if values.shape[0] < 1:
        raise ValueError("Window input needs at least one timestep")
    if values.shape[1] != expected_input_size:
        raise ValueError(f"Expected {expected_input_size} input features, got {values.shape[1]}")
    if not torch.isfinite(values).all():
        raise ValueError("Input window contains NaN or infinite values")
    return values.unsqueeze(0)


def _load_torch_checkpoint(path: Path, map_location: str | torch.device) -> Any:
    # ``weights_only=False`` is needed because our checkpoint includes benign
    # JSON-like metadata in addition to tensor weights. Callers must only load
    # checkpoints they trust, as with any pickle-backed PyTorch artifact.
    try:
        return torch.load(path, map_location=map_location, weights_only=False)
    except TypeError:  # PyTorch < 2.6
        return torch.load(path, map_location=map_location)


def _looks_like_state_dict(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(isinstance(item, Tensor) for item in value.values())
    )


def _checkpoint_model_spec(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("model_spec", "model_kwargs"):
        value = checkpoint.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    config = checkpoint.get("config")
    if isinstance(config, Mapping):
        nested_model = config.get("model", config)
        if isinstance(nested_model, Mapping):
            specification = dict(nested_model)
            if "input_size" in checkpoint:
                specification["input_size"] = checkpoint["input_size"]
            return specification
    return {
        key: checkpoint[key]
        for key in (
            "architecture",
            "input_size",
            "hidden_size",
            "num_layers",
            "dropout",
            "posture_classes",
        )
        if key in checkpoint
    }


def _as_model_mapping(config: Any) -> dict[str, Any]:
    if hasattr(config, "model"):
        config = config.model
    if isinstance(config, Mapping):
        return dict(config)
    if hasattr(config, "model_dump"):
        return dict(config.model_dump())
    return {
        key: getattr(config, key)
        for key in (
            "architecture",
            "input_size",
            "hidden_size",
            "num_layers",
            "dropout",
            "posture_classes",
        )
        if hasattr(config, key)
    }
