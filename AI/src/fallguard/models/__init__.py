"""Temporal fall-detection models and runtime prediction adapters."""

from fallguard.models.predictor import (
    TemporalClassifier,
    TemporalPredictor,
    load_predictor,
)
from fallguard.models.rule import (
    PoseRuleBaseline,
    RuleBasedClassifier,
    RuleBasedFallDetector,
    RuleConfig,
)
from fallguard.models.temporal import (
    MODEL_REGISTRY,
    MultiTaskTemporalModel,
    TemporalModel,
    TemporalOutput,
    available_models,
    build_model,
    create_model,
    register_model,
)

__all__ = [
    "MODEL_REGISTRY",
    "MultiTaskTemporalModel",
    "PoseRuleBaseline",
    "RuleBasedClassifier",
    "RuleBasedFallDetector",
    "RuleConfig",
    "TemporalClassifier",
    "TemporalModel",
    "TemporalOutput",
    "TemporalPredictor",
    "available_models",
    "build_model",
    "create_model",
    "load_predictor",
    "register_model",
]
