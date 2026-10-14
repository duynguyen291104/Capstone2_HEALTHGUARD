from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from fallguard.api.main import create_app
from fallguard.api.store import SQLiteEventStore
from fallguard.config import AppConfig
from fallguard.domain import PostureLabel, Prediction
from fallguard.inference import DummyClassifier
from fallguard.inference.pose import MediaPipePoseEstimator


def skeleton_payload(frame_count: int = 3) -> dict[str, Any]:
    landmark = [0.5, 0.5, 0.0, 1.0]
    return {
        "camera_id": "CAM-TEST",
        "person_id": "P-01",
        "fps": 20,
        "skeleton_sequence": [
            {
                "frame": index,
                "timestamp_ms": index * 50,
                "landmarks": [landmark for _ in range(33)],
            }
            for index in range(frame_count)
        ],
    }


class DisabledTelegram:
    enabled = False


def make_client(
    tmp_path: Path,
    *,
    video_processor: Any | None = None,
    telegram: Any | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            AppConfig(),
            classifier=DummyClassifier(),
            store=SQLiteEventStore(":memory:"),
            video_processor=video_processor,
            telegram=telegram if telegram is not None else DisabledTelegram(),  # type: ignore[arg-type]
            runtime_dir=tmp_path,
        )
    )


def test_health_model_dashboard_and_skeleton_validation(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["fallback_model"] is True

        model = client.get("/v1/model").json()
        assert model["outputs"] == ["posture", "fall_probability"]
        assert model["causal"] is True

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "FallGuard AI" in dashboard.text

        response = client.post("/v1/predict/skeleton", json=skeleton_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["activity"] == "UPRIGHT"
        assert body["fall_probability"] == 0.0
        assert body["system_state"] == "NORMAL"

        malformed = skeleton_payload(1)
        malformed["skeleton_sequence"][0]["landmarks"].pop()
        response = client.post("/v1/predict/skeleton", json=malformed)
        assert response.status_code == 422


def test_event_history_detail_feedback_and_metrics(tmp_path: Path) -> None:
    store = SQLiteEventStore(":memory:")
    store.upsert_event(
        {
            "event_id": "FALL-TEST-001",
            "state": "CONFIRMED_FALL",
            "risk_level": "HIGH",
            "timestamp_ms": 1_000,
            "camera_id": "CAM-01",
            "fall_probability": 0.96,
            "inactive_seconds": 8.4,
            "evidence": ["test evidence"],
            "metadata": {"smoothed_fall_probability": 0.9},
        }
    )
    app = create_app(
        AppConfig(),
        classifier=DummyClassifier(),
        store=store,
        telegram=DisabledTelegram(),  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(app) as client:
        listing = client.get("/v1/events")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["event_id"] == "FALL-TEST-001"

        detail = client.get("/v1/events/FALL-TEST-001")
        assert detail.status_code == 200
        assert detail.json()["evidence"] == ["test evidence"]
        assert client.get("/v1/events/missing").status_code == 404

        feedback = client.post(
            "/v1/events/FALL-TEST-001/feedback",
            json={"label": "false_alarm", "notes": "Caregiver review"},
        )
        assert feedback.status_code == 200
        assert feedback.json()["feedback"]["label"] == "false_alarm"

        metrics = client.get("/v1/metrics/summary").json()
        assert metrics["total_events"] == 1
        assert metrics["false_alarms"] == 1


class FakeVideoProcessor:
    def run_video(
        self,
        input_path: Path,
        *,
        output_video: Path,
        events_json: Path,
        target_fps: float,
        crop_right_half: bool,
        max_duration_seconds: float,
    ) -> dict[str, Any]:
        assert input_path.exists()
        assert target_fps == 20
        assert crop_right_half is True
        assert max_duration_seconds == 300.0
        output_video.write_bytes(b"annotated")
        events_json.write_text(json.dumps({"events": []}), encoding="utf-8")
        return {
            "source": str(input_path),
            "output_video": str(output_video),
            "events_json": str(events_json),
            "frame_count": 40,
            "pose_frame_count": 40,
            "prediction_count": 1,
            "duration_seconds": 2.0,
            "processing_fps": 50.0,
            "timeline": [],
            "events": [
                {
                    "event_id": "FALL-VIDEO-001",
                    "state": "CONFIRMED_FALL",
                    "risk_level": "HIGH",
                    "timestamp_ms": 2_000,
                    "inactive_seconds": 8.0,
                    "self_recovery": False,
                    "evidence": ["verified"],
                    "metadata": {"smoothed_fall_probability": 0.94},
                }
            ],
            "bounded": False,
        }


def test_bounded_video_upload_and_event_persistence(tmp_path: Path) -> None:
    with make_client(tmp_path, video_processor=FakeVideoProcessor()) as client:
        response = client.post(
            "/v1/predict/video?camera_id=CAM-UPLOAD&urfd_preview=true",
            files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["output_video_url"].startswith("/artifacts/")
        assert body["events_json_url"].startswith("/artifacts/")
        assert body["events"][0]["camera_id"] == "CAM-UPLOAD"
        assert client.get("/v1/events/FALL-VIDEO-001").status_code == 200

        unsupported = client.post(
            "/v1/predict/video",
            files={"file": ("clip.txt", b"x", "text/plain")},
        )
        assert unsupported.status_code == 415


class ScriptedFallClassifier:
    model_version = "scripted-test"
    is_fallback = False

    def predict(self, window: Any) -> Prediction:
        timestamp_ms = window[-1].timestamp_ms
        posture = PostureLabel.TRANSITION if timestamp_ms < 200 else PostureLabel.LYING
        probabilities = (
            (0.02, 0.96, 0.02) if posture is PostureLabel.TRANSITION else (0.02, 0.02, 0.96)
        )
        return Prediction(
            posture=posture,
            posture_probabilities=probabilities,
            fall_probability=0.96,
            timestamp_ms=timestamp_ms,
            model_version=self.model_version,
        )


class RecordingWebhook:
    enabled = True

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def notify(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


class RecordingTelegram:
    enabled = True

    def __init__(self) -> None:
        self.notifications: list[tuple[dict[str, Any], Path | None]] = []

    def notify(
        self,
        event: dict[str, Any],
        *,
        snapshot_jpeg: Path | None = None,
    ) -> bool:
        self.notifications.append((event, snapshot_jpeg))
        return True


def test_confirmed_skeleton_event_is_stored_and_alerts_are_deduplicated(
    tmp_path: Path,
) -> None:
    config = AppConfig.model_validate(
        {
            "data": {"window_size": 4, "stride": 1},
            "event_detection": {
                "ema_alpha": 1.0,
                "possible_fall_probability": 0.6,
                "confirm_fall_probability": 0.8,
                "possible_fall_min_seconds": 0.0,
                "fallen_min_seconds": 0.05,
                "inactive_seconds": 0.1,
                "recovery_upright_seconds": 0.1,
                "cooldown_seconds": 1.0,
            },
        }
    )
    webhook = RecordingWebhook()
    telegram = RecordingTelegram()
    store = SQLiteEventStore(":memory:")
    application = create_app(
        config,
        classifier=ScriptedFallClassifier(),
        store=store,
        webhook=webhook,  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(application) as client:
        response = client.post("/v1/predict/skeleton", json=skeleton_payload(9))

    assert response.status_code == 200
    event_id = response.json()["event_id"]
    assert event_id is not None
    assert store.get_event(event_id)["state"] == "CONFIRMED_FALL"  # type: ignore[index]
    assert [event["event_id"] for event in webhook.events] == [event_id]
    assert [(event["event_id"], snapshot) for event, snapshot in telegram.notifications] == [
        (event_id, None)
    ]


class TimelineAndFinalVideoProcessor(FakeVideoProcessor):
    def run_video(self, input_path: Path, **kwargs: Any) -> dict[str, Any]:
        result = super().run_video(input_path, **kwargs)
        event = dict(result["events"][0])
        event["metadata"] = {
            **event["metadata"],
            "just_confirmed": True,
        }
        result["timeline"] = [
            {
                "timestamp_ms": 2_000,
                "prediction": {
                    "timestamp_ms": 2_000,
                    "fall_probability": 0.94,
                },
                "event": event,
            }
        ]
        return result


class RecoveredFinalVideoProcessor(TimelineAndFinalVideoProcessor):
    def run_video(self, input_path: Path, **kwargs: Any) -> dict[str, Any]:
        result = super().run_video(input_path, **kwargs)
        result["events"][0] = {
            **result["events"][0],
            "state": "RECOVERED",
            "risk_level": "LOW",
            "timestamp_ms": 3_000,
            "self_recovery": True,
            "metadata": {
                "smoothed_fall_probability": 0.12,
                "recovered": True,
            },
        }
        return result


def test_video_alert_uses_one_annotated_snapshot_for_timeline_and_final_event(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    telegram = RecordingTelegram()
    webhook = RecordingWebhook()
    store = SQLiteEventStore(":memory:")

    def fake_extract(
        video_path: Path,
        destination: Path,
        *,
        timestamp_ms: int,
    ) -> Path:
        assert video_path.read_bytes() == b"annotated"
        assert timestamp_ms == 2_000
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg-evidence")
        return destination

    monkeypatch.setattr("fallguard.api.main.extract_video_snapshot", fake_extract)
    application = create_app(
        AppConfig(),
        classifier=DummyClassifier(),
        store=store,
        video_processor=TimelineAndFinalVideoProcessor(),
        webhook=webhook,  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/predict/video?camera_id=CAM-UPLOAD&urfd_preview=true",
            files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
        )

    assert response.status_code == 200
    assert [event["event_id"] for event in webhook.events] == ["FALL-VIDEO-001"]
    assert len(telegram.notifications) == 1
    event, snapshot = telegram.notifications[0]
    assert event["event_id"] == "FALL-VIDEO-001"
    assert snapshot is not None
    assert snapshot.parent == tmp_path / "results" / "snapshots"
    assert snapshot.name == "FALL-VIDEO-001.jpg"
    assert snapshot.read_bytes() == b"jpeg-evidence"
    persisted = store.get_event("FALL-VIDEO-001")
    assert persisted is not None
    assert persisted["metadata"]["snapshot_path"] == str(snapshot)


def test_video_alert_falls_back_to_text_when_snapshot_extraction_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    telegram = RecordingTelegram()
    store = SQLiteEventStore(":memory:")

    def fail_extract(*args: Any, **kwargs: Any) -> Path:
        raise ValueError("invalid annotated video")

    monkeypatch.setattr("fallguard.api.main.extract_video_snapshot", fail_extract)
    application = create_app(
        AppConfig(),
        classifier=DummyClassifier(),
        store=store,
        video_processor=FakeVideoProcessor(),
        telegram=telegram,  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/predict/video?camera_id=CAM-UPLOAD&urfd_preview=true",
            files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
        )

    assert response.status_code == 200
    assert len(telegram.notifications) == 1
    event, snapshot = telegram.notifications[0]
    assert event["event_id"] == "FALL-VIDEO-001"
    assert snapshot is None
    persisted = store.get_event("FALL-VIDEO-001")
    assert persisted is not None
    assert "snapshot_path" not in persisted["metadata"]


def test_video_alert_keeps_confirmation_payload_when_final_event_recovered(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    telegram = RecordingTelegram()
    store = SQLiteEventStore(":memory:")

    def fake_extract(
        video_path: Path,
        destination: Path,
        *,
        timestamp_ms: int,
    ) -> Path:
        assert timestamp_ms == 2_000
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"confirmation-frame")
        return destination

    monkeypatch.setattr("fallguard.api.main.extract_video_snapshot", fake_extract)
    application = create_app(
        AppConfig(),
        classifier=DummyClassifier(),
        store=store,
        video_processor=RecoveredFinalVideoProcessor(),
        telegram=telegram,  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/predict/video?urfd_preview=true",
            files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
        )

    assert response.status_code == 200
    assert len(telegram.notifications) == 1
    notification, snapshot = telegram.notifications[0]
    assert notification["state"] == "CONFIRMED_FALL"
    assert notification["risk_level"] == "HIGH"
    assert snapshot is not None

    persisted = store.get_event("FALL-VIDEO-001")
    assert persisted is not None
    assert persisted["state"] == "RECOVERED"
    assert persisted["metadata"]["recovered"] is True
    assert persisted["metadata"]["snapshot_path"] == str(snapshot)


def test_video_upload_enforces_byte_limit_before_processing(tmp_path: Path) -> None:
    config = AppConfig.model_validate({"api": {"max_upload_mb": 1}})

    class MustNotRun:
        def run_video(self, input_path: Path) -> dict[str, Any]:
            raise AssertionError(f"processor unexpectedly called for {input_path}")

    application = create_app(
        config,
        classifier=DummyClassifier(),
        store=SQLiteEventStore(":memory:"),
        video_processor=MustNotRun(),
        telegram=DisabledTelegram(),  # type: ignore[arg-type]
        runtime_dir=tmp_path,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/predict/video",
            files={"file": ("large.mp4", b"x" * (1024 * 1024 + 1), "video/mp4")},
        )
    assert response.status_code == 413


def test_default_pose_model_uses_downloaders_data_models_directory(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    model_dir = data_dir / "models"
    model_dir.mkdir(parents=True)
    full_model = model_dir / "pose_landmarker_full.task"
    lite_model = model_dir / "pose_landmarker_lite.task"
    full_model.write_bytes(b"model-placeholder")
    lite_model.write_bytes(b"model-placeholder")
    config = AppConfig.model_validate(
        {
            "paths": {
                "raw_dir": str(data_dir / "raw"),
                "runtime_dir": str(tmp_path / "runtime"),
            }
        }
    )

    application = create_app(
        config,
        classifier=DummyClassifier(),
        store=SQLiteEventStore(":memory:"),
        telegram=DisabledTelegram(),  # type: ignore[arg-type]
    )

    estimator = application.state.pose_estimator
    assert isinstance(estimator, MediaPipePoseEstimator)
    assert estimator.model_path == full_model
