from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

import torch
from torch import Tensor, nn
from torch.nn.utils.rnn import pack_padded_sequence


class TemporalOutput(NamedTuple):
    """Logits produced by the two supervised heads.

    A ``NamedTuple`` deliberately keeps the output convenient for both styles
    commonly used by callers::

        posture_logits, event_logits = model(windows)
        event_logits = model(windows).event_logits
    """

    posture_logits: Tensor
    event_logits: Tensor

    @property
    def fall_logits(self) -> Tensor:
        """Compatibility alias for the fall-event head."""

        return self.event_logits


class TemporalEncoder(nn.Module):
    """Base class for causal sequence encoders."""

    output_size: int

    def forward(self, inputs: Tensor, lengths: Tensor | None = None) -> Tensor:
        """Return one causal representation per input window."""

        raise NotImplementedError


class RecurrentEncoder(TemporalEncoder):
    """Unidirectional GRU/LSTM encoder suitable for streaming inference."""

    def __init__(
        self,
        architecture: str,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        dropout: float,
    ) -> None:
        super().__init__()
        recurrent_type: type[nn.GRU] | type[nn.LSTM]
        if architecture == "gru":
            recurrent_type = nn.GRU
        elif architecture == "lstm":
            recurrent_type = nn.LSTM
        else:  # pragma: no cover - guarded by the registry
            raise ValueError(f"Unsupported recurrent architecture: {architecture}")

        self.recurrent = recurrent_type(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
            bidirectional=False,
        )
        self.output_size = hidden_size

    def forward(self, inputs: Tensor, lengths: Tensor | None = None) -> Tensor:
        if lengths is None:
            _, hidden = self.recurrent(inputs)
        else:
            safe_lengths = _validate_lengths(lengths, inputs)
            packed = pack_padded_sequence(
                inputs,
                safe_lengths.detach().cpu(),
                batch_first=True,
                enforce_sorted=False,
            )
            _, hidden = self.recurrent(packed)

        # LSTM returns (hidden_state, cell_state); the final layer's hidden
        # state is causal and summarizes only frames available in the window.
        hidden_state = hidden[0] if isinstance(hidden, tuple) else hidden
        return hidden_state[-1]


class CausalConv1d(nn.Conv1d):
    """One-dimensional convolution that never reads future timesteps."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        *,
        dilation: int = 1,
    ) -> None:
        self.left_padding = (kernel_size - 1) * dilation
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            padding=self.left_padding,
            dilation=dilation,
        )

    def forward(self, inputs: Tensor) -> Tensor:
        output = super().forward(inputs)
        if self.left_padding:
            output = output[..., : -self.left_padding]
        return output


class TemporalResidualBlock(nn.Module):
    def __init__(self, channels: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.convolution_1 = CausalConv1d(
            channels,
            channels,
            kernel_size=3,
            dilation=dilation,
        )
        self.convolution_2 = CausalConv1d(
            channels,
            channels,
            kernel_size=3,
            dilation=dilation,
        )
        # LayerNorm is applied independently at each timestep, unlike
        # BatchNorm over time, and therefore preserves the causal contract.
        self.normalization_1 = nn.LayerNorm(channels)
        self.normalization_2 = nn.LayerNorm(channels)
        self.activation = nn.GELU()
        self.dropout = nn.Dropout(dropout)

    def forward(self, inputs: Tensor) -> Tensor:
        residual = inputs
        output = self.convolution_1(inputs)
        output = self.normalization_1(output.transpose(1, 2)).transpose(1, 2)
        output = self.dropout(self.activation(output))
        output = self.convolution_2(output)
        output = self.normalization_2(output.transpose(1, 2)).transpose(1, 2)
        output = self.dropout(self.activation(output))
        return self.activation(output + residual)


class TCNEncoder(TemporalEncoder):
    """Dilated causal temporal convolutional encoder."""

    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.input_projection = nn.Conv1d(input_size, hidden_size, kernel_size=1)
        self.blocks = nn.ModuleList(
            TemporalResidualBlock(hidden_size, dilation=2**layer, dropout=dropout)
            for layer in range(num_layers)
        )
        self.output_size = hidden_size

    def encode_sequence(self, inputs: Tensor) -> Tensor:
        output = self.input_projection(inputs.transpose(1, 2))
        for block in self.blocks:
            output = block(output)
        return output.transpose(1, 2)

    def forward(self, inputs: Tensor, lengths: Tensor | None = None) -> Tensor:
        sequence = self.encode_sequence(inputs)
        if lengths is None:
            return sequence[:, -1]
        safe_lengths = _validate_lengths(lengths, inputs).to(sequence.device)
        row_indices = torch.arange(sequence.shape[0], device=sequence.device)
        return sequence[row_indices, safe_lengths - 1]


EncoderBuilder = Callable[[int, int, int, float], TemporalEncoder]
MODEL_REGISTRY: dict[str, EncoderBuilder] = {}


def register_model(name: str, builder: EncoderBuilder, *, replace: bool = False) -> None:
    """Register a temporal encoder factory.

    Third-party experiments can register a new encoder without changing the
    multi-task heads or the training code.
    """

    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("Model name must not be empty")
    if normalized_name in MODEL_REGISTRY and not replace:
        raise ValueError(f"Model architecture is already registered: {normalized_name}")
    MODEL_REGISTRY[normalized_name] = builder


def available_models() -> tuple[str, ...]:
    return tuple(sorted(MODEL_REGISTRY))


def _recurrent_builder(architecture: str) -> EncoderBuilder:
    def builder(
        input_size: int,
        hidden_size: int,
        num_layers: int,
        dropout: float,
    ) -> TemporalEncoder:
        return RecurrentEncoder(
            architecture,
            input_size,
            hidden_size,
            num_layers,
            dropout,
        )

    return builder


register_model("gru", _recurrent_builder("gru"))
register_model("lstm", _recurrent_builder("lstm"))
register_model("tcn", TCNEncoder)


class MultiTaskTemporalModel(nn.Module):
    """Shared causal encoder with posture and fall-event classification heads."""

    def __init__(
        self,
        input_size: int,
        *,
        architecture: str = "gru",
        hidden_size: int = 96,
        num_layers: int = 2,
        dropout: float = 0.30,
        posture_classes: int = 3,
    ) -> None:
        super().__init__()
        if input_size < 1:
            raise ValueError("input_size must be positive")
        if hidden_size < 1:
            raise ValueError("hidden_size must be positive")
        if num_layers < 1:
            raise ValueError("num_layers must be positive")
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if posture_classes < 2:
            raise ValueError("posture_classes must be at least 2")

        normalized_architecture = architecture.strip().lower()
        try:
            builder = MODEL_REGISTRY[normalized_architecture]
        except KeyError as error:
            choices = ", ".join(available_models())
            raise ValueError(
                f"Unknown model architecture {architecture!r}; available: {choices}"
            ) from error

        self.input_size = int(input_size)
        self.architecture = normalized_architecture
        self.hidden_size = int(hidden_size)
        self.num_layers = int(num_layers)
        self.dropout_probability = float(dropout)
        self.posture_classes = int(posture_classes)
        self.encoder = builder(input_size, hidden_size, num_layers, dropout)
        self.head_dropout = nn.Dropout(dropout)
        self.posture_head = nn.Linear(self.encoder.output_size, posture_classes)
        self.event_head = nn.Linear(self.encoder.output_size, 1)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.xavier_uniform_(self.posture_head.weight)
        nn.init.zeros_(self.posture_head.bias)
        nn.init.xavier_uniform_(self.event_head.weight)
        nn.init.zeros_(self.event_head.bias)

    def encode(self, inputs: Tensor, lengths: Tensor | None = None) -> Tensor:
        _validate_inputs(inputs, self.input_size)
        return self.encoder(inputs, lengths)

    def forward(
        self,
        inputs: Tensor,
        lengths: Tensor | None = None,
    ) -> TemporalOutput:
        shared_features = self.head_dropout(self.encode(inputs, lengths))
        posture_logits = self.posture_head(shared_features)
        event_logits = self.event_head(shared_features).squeeze(-1)
        return TemporalOutput(posture_logits, event_logits)

    def checkpoint_spec(self) -> dict[str, Any]:
        """Architecture fields required to reconstruct this model."""

        return {
            "architecture": self.architecture,
            "input_size": self.input_size,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "dropout": self.dropout_probability,
            "posture_classes": self.posture_classes,
        }


# A concise compatibility name used by some callers and notebooks.
TemporalModel = MultiTaskTemporalModel


def build_model(
    config: Any = None,
    input_size: int | None = None,
    **overrides: Any,
) -> MultiTaskTemporalModel:
    """Build a registered multi-task model from a config object or mapping.

    ``config`` may be an ``AppConfig``, its nested ``model`` config, a mapping,
    an architecture name, or ``None``. Explicit keyword overrides win.
    """

    values = _model_values(config)
    values.update({key: value for key, value in overrides.items() if value is not None})
    if input_size is not None:
        values["input_size"] = input_size
    if "input_size" not in values:
        raise ValueError("input_size is required and should be inferred from the dataset")

    allowed = {
        "input_size",
        "architecture",
        "hidden_size",
        "num_layers",
        "dropout",
        "posture_classes",
    }
    return MultiTaskTemporalModel(**{key: value for key, value in values.items() if key in allowed})


create_model = build_model


def _model_values(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, str):
        return {"architecture": config}
    if hasattr(config, "model"):
        config = config.model
    if isinstance(config, Mapping):
        return dict(config)

    field_names = (
        "architecture",
        "input_size",
        "hidden_size",
        "num_layers",
        "dropout",
        "posture_classes",
    )
    return {name: getattr(config, name) for name in field_names if hasattr(config, name)}


def _validate_inputs(inputs: Tensor, expected_features: int) -> None:
    if inputs.ndim != 3:
        raise ValueError(
            "Temporal model input must have shape [batch, time, features], "
            f"got {tuple(inputs.shape)}"
        )
    if inputs.shape[1] < 1:
        raise ValueError("Temporal model input needs at least one timestep")
    if inputs.shape[2] != expected_features:
        raise ValueError(f"Expected {expected_features} features, got {inputs.shape[2]}")


def _validate_lengths(lengths: Tensor, inputs: Tensor) -> Tensor:
    safe_lengths = torch.as_tensor(lengths, dtype=torch.long, device=inputs.device)
    if safe_lengths.ndim != 1 or safe_lengths.shape[0] != inputs.shape[0]:
        raise ValueError("lengths must have shape [batch]")
    if torch.any(safe_lengths < 1) or torch.any(safe_lengths > inputs.shape[1]):
        raise ValueError("lengths must be between 1 and the input sequence length")
    return safe_lengths
