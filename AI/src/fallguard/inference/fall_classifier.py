"""Compatibility import for temporal classifier adapters."""

from fallguard.inference.classifier import (
    ClassifierProtocol,
    DummyClassifier,
    TemporalClassifier,
    TorchTemporalClassifier,
)

__all__ = [
    "ClassifierProtocol",
    "DummyClassifier",
    "TemporalClassifier",
    "TorchTemporalClassifier",
]
