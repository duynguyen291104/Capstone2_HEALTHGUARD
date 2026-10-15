from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from typer.testing import CliRunner

import fallguard.cli as cli_module
from fallguard.api.store import SQLiteEventStore
from fallguard.cli import app
from fallguard.config import load_config
from fallguard.domain import EventUpdate, PostureLabel, Prediction, RiskLevel, SystemState
from fallguard.notifications import (
    TelegramChatInfo,
    TelegramDeliveryResult,
    TelegramNotificationResult,
    TelegramNotifier,
)

runner = CliRunner()


def test_cli_help_and_version() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "FallGuard" in help_result.stdout
    assert "prepare-urfd" in runner.invoke(app, ["data", "--help"]).stdout

    version_result = runner.invoke(app, ["--version"])
    assert version_result.exit_code == 0
    assert "fallguard-ai 0.1.0" in version_result.stdout


def test_doctor_uses_built_in_config_outside_repository(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FALLGUARD_CONFIG", raising=False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "built-in defaults" in result.stdout


def test_urfd_download_requires_explicit_license(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["data", "download-urfd", "--output", str(tmp_path / "urfd")],
    )
    assert result.exit_code == 2
    assert "CC BY-NC-SA 4.0" in result.stdout
    assert not (tmp_path / "urfd").exists()


def test_synthetic_command_materializes_all_splits(tmp_path: Path) -> None:
    destination = tmp_path / "synthetic"
    result = runner.invoke(
        app,
        [
            "data",
            "synthetic",
            "--output",
            str(destination),
            "--sequences",
            "6",
            "--frames",
            "45",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (destination / "sequences.csv").is_file()
    for split in ("train", "validation", "test"):
        assert (destination / f"windows_{split}.npz").is_file()


def test_telegram_discover_lists_recent_chat(monkeypatch) -> None:
    notifier = SimpleNamespace(
        discover_recent_chats=lambda: (
            TelegramChatInfo(
                chat_id="123456",
                chat_type="private",
                title="Capstone Demo",
                username="capstone_user",
            ),
        )
    )
    monkeypatch.setattr(
        TelegramNotifier,
        "from_env",
        classmethod(lambda cls: notifier),
    )

    result = runner.invoke(app, ["telegram", "discover"])

    assert result.exit_code == 0, result.output
    assert "123456" in result.stdout
    assert "Capstone Demo" in result.stdout
    assert "@capstone_user" in result.stdout


def test_telegram_discover_explains_start_when_no_updates(monkeypatch) -> None:
    notifier = SimpleNamespace(discover_recent_chats=lambda: ())
    monkeypatch.setattr(
        TelegramNotifier,
        "from_env",
        classmethod(lambda cls: notifier),
    )

    result = runner.invoke(app, ["telegram", "discover"])

    assert result.exit_code == 0, result.output
    assert "/start" in result.stdout


def test_telegram_test_sends_optional_snapshot(tmp_path: Path, monkeypatch) -> None:
    snapshot = tmp_path / "sample.jpg"
    snapshot.write_bytes(b"test-jpeg")
    calls: list[tuple[dict, Path | None]] = []

    class RecordingNotifier:
        enabled = True

        def notify(self, event, *, snapshot_jpeg=None):
            calls.append((event, snapshot_jpeg))
            return TelegramNotificationResult(
                enabled=True,
                deliveries=(
                    TelegramDeliveryResult(
                        chat_id="123456",
                        method="sendPhoto",
                        success=True,
                        status_code=200,
                        message_id=7,
                    ),
                ),
            )

    notifier = RecordingNotifier()
    monkeypatch.setattr(
        TelegramNotifier,
        "from_env",
        classmethod(lambda cls: notifier),
    )

    result = runner.invoke(
        app,
        ["telegram", "test", "--snapshot", str(snapshot)],
    )

    assert result.exit_code == 0, result.output
    assert "Đã gửi" in result.stdout
    assert len(calls) == 1
    assert calls[0][0]["state"] == "CONFIRMED_FALL"
    assert calls[0][1] == snapshot


def test_camera_persists_safe_snapshot_and_deduplicates_telegram(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import cv2

    settings = load_config(None)
    settings.paths.runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(cli_module, "load_config", lambda _: settings)

    class FakeEstimator:
        closed = False

        def close(self) -> None:
            self.closed = True

    estimator = FakeEstimator()
    monkeypatch.setattr(
        cli_module,
        "_runtime_components",
        lambda settings, *, checkpoint, pose_model: (object(), estimator, None),
    )

    event = EventUpdate(
        state=SystemState.CONFIRMED_FALL,
        risk_level=RiskLevel.HIGH,
        timestamp_ms=1_000,
        event_id="../../FALL:CAMERA:1",
        inactive_seconds=8.0,
        evidence=["confirmed"],
        metadata={
            "just_confirmed": True,
            "posture": "LYING",
            "smoothed_fall_probability": 0.97,
        },
    )
    prediction = Prediction(
        posture=PostureLabel.LYING,
        posture_probabilities=(0.01, 0.02, 0.97),
        fall_probability=0.98,
        timestamp_ms=1_000,
    )
    inference = SimpleNamespace(event=event, prediction=prediction)

    class FakePipeline:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def process_frame(self, frame, frame_index, timestamp_ms):
            return inference

        def annotate_frame(self, frame, result):
            return frame

    monkeypatch.setattr(
        "fallguard.inference.pipeline.InferencePipeline",
        FakePipeline,
    )

    frames = [
        np.zeros((24, 32, 3), dtype=np.uint8),
        np.zeros((24, 32, 3), dtype=np.uint8),
    ]

    class FakeCapture:
        released = False

        def isOpened(self) -> bool:
            return True

        def read(self):
            return (True, frames.pop(0)) if frames else (False, None)

        def release(self) -> None:
            self.released = True

    capture = FakeCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda source: capture)
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "waitKey", lambda delay: -1)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    clock = iter((0.0, 0.1, 0.2))
    monkeypatch.setattr(cli_module.time, "perf_counter", lambda: next(clock))

    notifications: list[tuple[dict, Path | None]] = []

    class RecordingNotifier:
        enabled = True

        def notify(self, stored_event, *, snapshot_jpeg=None):
            notifications.append((stored_event, snapshot_jpeg))
            return TelegramNotificationResult(
                enabled=True,
                deliveries=(
                    TelegramDeliveryResult(
                        chat_id="123456",
                        method="sendPhoto",
                        success=True,
                    ),
                ),
            )

    notifier = RecordingNotifier()
    monkeypatch.setattr(
        TelegramNotifier,
        "from_env",
        classmethod(lambda cls: notifier),
    )

    result = runner.invoke(
        app,
        [
            "infer",
            "camera",
            "--camera-id",
            "CAM-DEMO",
            "--person-id",
            "PERSON-1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert capture.released
    assert estimator.closed
    assert len(notifications) == 1
    stored_event, snapshot_path = notifications[0]
    assert stored_event["created_at"]
    assert stored_event["camera_id"] == "CAM-DEMO"
    assert stored_event["person_id"] == "PERSON-1"
    assert snapshot_path is not None and snapshot_path.is_file()
    assert snapshot_path.parent == settings.paths.runtime_dir / "results" / "snapshots"
    assert ".." not in snapshot_path.name

    store = SQLiteEventStore(settings.paths.runtime_dir / "fallguard.db")
    try:
        persisted = store.get_event("../../FALL:CAMERA:1")
    finally:
        store.close()
    assert persisted is not None
    assert persisted["metadata"]["snapshot_path"] == str(snapshot_path.resolve())
