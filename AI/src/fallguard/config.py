from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ProjectConfig(BaseModel):
    name: str = "FallGuard AI"
    seed: int = 42
    device: Literal["auto", "cpu", "cuda"] = "auto"


class PathConfig(BaseModel):
    raw_dir: Path = Path("data/raw")
    interim_dir: Path = Path("data/interim")
    processed_dir: Path = Path("data/processed")
    manifest_dir: Path = Path("data/manifests")
    artifact_dir: Path = Path("artifacts")
    runtime_dir: Path = Path("runtime")


class SplitConfig(BaseModel):
    train: float = 0.70
    validation: float = 0.15
    test: float = 0.15

    @model_validator(mode="after")
    def validate_total(self) -> SplitConfig:
        if abs(self.train + self.validation + self.test - 1.0) > 1e-6:
            raise ValueError("data.splits values must sum to 1.0")
        return self


class DataConfig(BaseModel):
    dataset: str = "urfd"
    camera: str = "cam0"
    target_fps: int = Field(20, gt=0)
    window_size: int = Field(40, ge=4)
    stride: int = Field(5, ge=1)
    max_interpolation_gap: int = Field(3, ge=0)
    min_pose_visibility: float = Field(0.35, ge=0.0, le=1.0)
    pose_model_variant: Literal["lite", "full", "heavy"] = "full"
    min_pose_detection_confidence: float = Field(0.35, ge=0.0, le=1.0)
    min_pose_presence_confidence: float = Field(0.35, ge=0.0, le=1.0)
    min_pose_tracking_confidence: float = Field(0.35, ge=0.0, le=1.0)
    splits: SplitConfig = SplitConfig()


class FeatureConfig(BaseModel):
    include_xyz: bool = True
    include_visibility: bool = True
    include_velocity: bool = True
    include_geometry: bool = True
    normalize_center: Literal["hip"] = "hip"
    normalize_scale: Literal["torso", "shoulders"] = "torso"


class ModelConfig(BaseModel):
    architecture: Literal["lstm", "gru", "tcn"] = "gru"
    hidden_size: int = Field(96, ge=8)
    num_layers: int = Field(2, ge=1)
    dropout: float = Field(0.30, ge=0.0, lt=1.0)
    posture_classes: int = Field(3, ge=2)
    posture_loss_weight: float = Field(0.30, ge=0.0)


class TrainingConfig(BaseModel):
    batch_size: int = Field(32, ge=1)
    epochs: int = Field(30, ge=1)
    learning_rate: float = Field(0.001, gt=0)
    weight_decay: float = Field(0.0001, ge=0)
    patience: int = Field(7, ge=1)
    num_workers: int = Field(0, ge=0)
    weighted_loss: bool = True


class EventDetectionConfig(BaseModel):
    ema_alpha: float = Field(0.40, gt=0.0, le=1.0)
    possible_fall_probability: float = Field(0.70, ge=0.0, le=1.0)
    confirm_fall_probability: float = Field(0.82, ge=0.0, le=1.0)
    possible_fall_min_seconds: float = Field(0.25, ge=0.0)
    fallen_min_seconds: float = Field(2.0, ge=0.0)
    inactive_seconds: float = Field(8.0, ge=0.0)
    recovery_upright_seconds: float = Field(1.5, ge=0.0)
    cooldown_seconds: float = Field(20.0, ge=0.0)


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    max_upload_mb: int = Field(250, ge=1)
    cors_origins: list[str] = []


class AppConfig(BaseModel):
    project: ProjectConfig = ProjectConfig()
    paths: PathConfig = PathConfig()
    data: DataConfig = DataConfig()
    features: FeatureConfig = FeatureConfig()
    model: ModelConfig = ModelConfig()
    training: TrainingConfig = TrainingConfig()
    event_detection: EventDetectionConfig = EventDetectionConfig()
    api: ApiConfig = ApiConfig()


DEFAULT_CONFIG_PATH = Path("configs/mvp.yaml")


def resolve_config_path(path: str | Path | None = None) -> Path | None:
    """Resolve an explicit, environment, or repository-local configuration."""

    if path is not None:
        return Path(path)
    environment_path = os.getenv("FALLGUARD_CONFIG", "").strip()
    if environment_path:
        return Path(environment_path)
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return None


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = resolve_config_path(path)
    if config_path is None:
        return AppConfig()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw)
