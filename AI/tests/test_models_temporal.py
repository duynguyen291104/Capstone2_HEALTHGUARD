from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from fallguard.domain import PostureLabel, Prediction
from fallguard.models import (
    RuleBasedFallDetector,
    TemporalPredictor,
    available_models,
    build_model,
)
from fallguard.models.temporal import TCNEncoder


@pytest.mark.parametrize("architecture", ("gru", "lstm", "tcn"))
def test_temporal_architectures_return_two_heads(architecture: str) -> None:
    model = build_model(
        architecture,
        input_size=7,
        hidden_size=8,
        num_layers=2,
        dropout=0.1,
        posture_classes=3,
    )
    output = model(torch.randn(4, 9, 7))

    posture_logits, event_logits = output
    assert posture_logits.shape == (4, 3)
    assert event_logits.shape == (4,)
    assert output.fall_logits is output.event_logits
    assert {"gru", "lstm", "tcn"}.issubset(available_models())


def test_tcn_sequence_is_causal() -> None:
    torch.manual_seed(4)
    encoder = TCNEncoder(input_size=3, hidden_size=8, num_layers=2, dropout=0.0)
    encoder.eval()
    original = torch.randn(1, 12, 3)
    changed_future = original.clone()
    changed_future[:, 7:] += 100.0

    with torch.inference_mode():
        encoded_original = encoder.encode_sequence(original)
        encoded_changed = encoder.encode_sequence(changed_future)

    torch.testing.assert_close(encoded_original[:, :7], encoded_changed[:, :7])


def test_predictor_reconstructs_checkpoint_and_returns_domain_prediction(
    tmp_path: Path,
) -> None:
    model = build_model(
        "gru",
        input_size=5,
        hidden_size=8,
        num_layers=1,
        dropout=0.0,
        posture_classes=3,
    )
    checkpoint = tmp_path / "model.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "model_spec": model.checkpoint_spec(),
            "model_version": "synthetic-v1",
        },
        checkpoint,
    )

    classifier = TemporalPredictor.from_checkpoint(checkpoint, device="cpu")
    prediction = classifier.predict(np.zeros((6, 5), dtype=np.float32), timestamp_ms=800)

    assert isinstance(prediction, Prediction)
    assert isinstance(prediction.posture, PostureLabel)
    assert prediction.timestamp_ms == 800
    assert prediction.model_version == "synthetic-v1"
    assert sum(prediction.posture_probabilities) == pytest.approx(1.0)
    assert 0.0 <= prediction.fall_probability <= 1.0
    assert classifier.metadata()["input_size"] == 5


def test_predictor_accepts_one_frame_structured_feature_window() -> None:
    model = build_model(
        "gru",
        input_size=132,
        hidden_size=8,
        num_layers=1,
        dropout=0.0,
        posture_classes=3,
    )
    classifier = TemporalPredictor(model, device="cpu")

    prediction = classifier.predict(np.zeros((1, 33, 4), dtype=np.float32))

    assert len(prediction.posture_probabilities) == 3


def test_rule_baseline_responds_to_drop_and_horizontal_pose() -> None:
    upright = np.zeros((8, 33, 4), dtype=np.float32)
    upright[..., 3] = 1.0
    upright[:, 11, :2] = (0.45, 0.20)
    upright[:, 12, :2] = (0.55, 0.20)
    upright[:, 23, :2] = (0.47, 0.55)
    upright[:, 24, :2] = (0.53, 0.55)

    falling = upright.copy()
    falling[-1, 11, :2] = (0.25, 0.78)
    falling[-1, 12, :2] = (0.25, 0.82)
    falling[-1, 23, :2] = (0.65, 0.78)
    falling[-1, 24, :2] = (0.65, 0.82)
    timestamps = np.arange(8) * 50

    baseline = RuleBasedFallDetector()
    normal_prediction = baseline.predict(upright, timestamps_ms=timestamps)
    fall_prediction = baseline.predict(falling, timestamps_ms=timestamps)

    assert normal_prediction.posture is PostureLabel.UPRIGHT
    assert fall_prediction.posture is PostureLabel.LYING
    assert fall_prediction.fall_probability > normal_prediction.fall_probability
    assert fall_prediction.evidence
