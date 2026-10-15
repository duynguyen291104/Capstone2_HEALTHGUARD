from __future__ import annotations

from typing import Any, NamedTuple

import numpy as np
import torch
from torch import Tensor, nn
from torch.nn import functional as F

from fallguard.models.predictor import unpack_temporal_output


class LossOutput(NamedTuple):
    total: Tensor
    posture: Tensor
    event: Tensor


class MultiTaskLoss(nn.Module):
    """Weighted posture CE plus fall-event BCE-with-logits."""

    def __init__(
        self,
        *,
        posture_loss_weight: float = 0.30,
        posture_class_weights: Tensor | np.ndarray | None = None,
        event_pos_weight: Tensor | float | None = None,
    ) -> None:
        super().__init__()
        if posture_loss_weight < 0:
            raise ValueError("posture_loss_weight must be non-negative")
        self.posture_loss_weight = float(posture_loss_weight)
        posture_weights = (
            torch.as_tensor(posture_class_weights, dtype=torch.float32)
            if posture_class_weights is not None
            else None
        )
        positive_weight = (
            torch.as_tensor(event_pos_weight, dtype=torch.float32).reshape(1)
            if event_pos_weight is not None
            else None
        )
        self.register_buffer("posture_class_weights", posture_weights)
        self.register_buffer("event_pos_weight", positive_weight)

    def forward(self, *args: Any) -> LossOutput:
        """Support ``(output, posture_y, event_y)`` and four-tensor style."""

        if len(args) == 3:
            output, posture_targets, event_targets = args
            posture_logits, event_logits = unpack_temporal_output(output)
        elif len(args) == 4:
            posture_logits, event_logits, posture_targets, event_targets = args
        else:
            raise TypeError(
                "MultiTaskLoss expects (output, posture_targets, event_targets) "
                "or (posture_logits, event_logits, posture_targets, event_targets)"
            )

        posture_targets = torch.as_tensor(
            posture_targets,
            dtype=torch.long,
            device=posture_logits.device,
        ).reshape(-1)
        event_targets = torch.as_tensor(
            event_targets,
            dtype=event_logits.dtype,
            device=event_logits.device,
        ).reshape(-1)
        event_logits = event_logits.reshape(-1)
        if posture_logits.ndim != 2 or posture_logits.shape[0] != posture_targets.shape[0]:
            raise ValueError("posture logits/targets have incompatible shapes")
        if event_logits.shape != event_targets.shape:
            raise ValueError("event logits/targets have incompatible shapes")

        posture_loss = F.cross_entropy(
            posture_logits,
            posture_targets,
            weight=self.posture_class_weights,
        )
        event_loss = F.binary_cross_entropy_with_logits(
            event_logits,
            event_targets,
            pos_weight=self.event_pos_weight,
        )
        total_loss = event_loss + self.posture_loss_weight * posture_loss
        return LossOutput(total_loss, posture_loss, event_loss)


def balanced_class_weights(
    labels: np.ndarray | Tensor,
    num_classes: int | None = None,
) -> Tensor:
    """Inverse-frequency weights normalized to mean one over present classes."""

    values = torch.as_tensor(labels, dtype=torch.long).reshape(-1)
    if values.numel() == 0:
        raise ValueError("Cannot compute class weights from an empty label array")
    if torch.any(values < 0):
        raise ValueError("Class labels must be non-negative")
    if num_classes is None:
        num_classes = int(values.max().item()) + 1
    if num_classes < 1 or torch.any(values >= num_classes):
        raise ValueError("num_classes does not cover all observed labels")

    counts = torch.bincount(values, minlength=num_classes).to(torch.float32)
    present = counts > 0
    weights = torch.zeros(num_classes, dtype=torch.float32)
    weights[present] = values.numel() / (present.sum() * counts[present])
    weights[present] /= weights[present].mean()
    return weights


def event_positive_weight(labels: np.ndarray | Tensor) -> Tensor:
    """Return the BCE positive-class multiplier (negative/positive count)."""

    values = torch.as_tensor(labels, dtype=torch.float32).reshape(-1)
    if values.numel() == 0:
        raise ValueError("Cannot compute event weight from an empty label array")
    if not torch.all((values == 0) | (values == 1)):
        raise ValueError("Event labels must be binary")
    positives = int((values == 1).sum())
    negatives = int((values == 0).sum())
    if positives == 0 or negatives == 0:
        # Weighting cannot recover a class absent from the data. A neutral
        # multiplier is stable and metadata still records the class counts.
        return torch.tensor(1.0, dtype=torch.float32)
    return torch.tensor(negatives / positives, dtype=torch.float32)


compute_class_weights = balanced_class_weights
