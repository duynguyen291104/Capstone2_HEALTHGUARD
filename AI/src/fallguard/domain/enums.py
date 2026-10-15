from __future__ import annotations

from enum import IntEnum, StrEnum


class PostureLabel(IntEnum):
    """Posture labels present in URFD's public depth-derived annotations."""

    UPRIGHT = 0
    TRANSITION = 1
    LYING = 2


class SystemState(StrEnum):
    NORMAL = "NORMAL"
    POSSIBLE_FALL = "POSSIBLE_FALL"
    VERIFYING = "VERIFYING"
    CONFIRMED_FALL = "CONFIRMED_FALL"
    RECOVERED = "RECOVERED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
