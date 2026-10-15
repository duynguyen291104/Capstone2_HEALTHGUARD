from __future__ import annotations

import importlib
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from fallguard.config import AppConfig, load_config, resolve_config_path


@dataclass(slots=True, frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _module_check(module_name: str, display_name: str | None = None) -> CheckResult:
    name = display_name or module_name
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", "installed")
        return CheckResult(name, True, str(version))
    except Exception as exc:  # dependency diagnostics must not crash the command
        return CheckResult(name, False, f"{type(exc).__name__}: {exc}")


def _path_checks(config: AppConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    for field_name, path in config.paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(path.resolve())
            free_gib = usage.free / (1024**3)
            results.append(
                CheckResult(
                    f"path:{field_name}",
                    free_gib >= 5.0,
                    f"{path.resolve()} ({free_gib:.1f} GiB free)",
                    required=field_name not in {"artifact_dir", "runtime_dir"},
                )
            )
        except OSError as exc:
            results.append(CheckResult(f"path:{field_name}", False, str(exc)))
    return results


def run_doctor(config_path: str | Path | None = None) -> list[CheckResult]:
    results = [
        CheckResult(
            "python",
            (3, 11) <= sys.version_info[:2] < (3, 15),
            f"{platform.python_version()} ({sys.executable})",
        ),
        _module_check("numpy"),
        _module_check("cv2", "opencv"),
        _module_check("mediapipe"),
        _module_check("torch"),
        _module_check("fastapi"),
    ]

    try:
        import torch

        if torch.cuda.is_available():
            detail = f"{torch.cuda.get_device_name(0)} / CUDA {torch.version.cuda}"
            results.append(CheckResult("accelerator", True, detail, required=False))
        else:
            results.append(
                CheckResult(
                    "accelerator",
                    True,
                    "CUDA unavailable; CPU fallback will be used",
                    required=False,
                )
            )
    except Exception as exc:
        results.append(CheckResult("accelerator", False, str(exc), required=False))

    config: AppConfig | None = None
    try:
        resolved_config = resolve_config_path(config_path)
        config = load_config(config_path)
        config_detail = (
            str(resolved_config.resolve())
            if resolved_config is not None
            else "built-in defaults"
        )
        results.append(CheckResult("config", True, config_detail))
        results.extend(_path_checks(config))
    except Exception as exc:
        results.append(CheckResult("config", False, f"{type(exc).__name__}: {exc}"))

    pose_variant = config.data.pose_model_variant if config is not None else "full"
    pose_model = Path(f"data/models/pose_landmarker_{pose_variant}.task")
    results.append(
        CheckResult(
            "pose-model",
            pose_model.exists(),
            str(pose_model.resolve()) if pose_model.exists() else "downloaded on first prepare run",
            required=False,
        )
    )
    return results


def doctor_is_healthy(results: list[CheckResult]) -> bool:
    return all(item.ok for item in results if item.required)
