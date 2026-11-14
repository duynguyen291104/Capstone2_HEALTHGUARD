from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np

import healthguard_service
from healthguard_service import create_app

KEY = "fake-service-key-for-automated-testing"
HEADERS = {"X-FallGuard-Key": KEY}


def test_service_rejects_untrusted_clients():
    with TestClient(create_app(KEY)) as client:
        assert client.get("/health").status_code == 403
        assert client.get("/health", headers=HEADERS).status_code == 200


def test_camera_session_is_bound_to_requester(monkeypatch):
    # Exercise ownership and stop protocol without opening a real webcam.
    api = create_app(KEY)
    with TestClient(api) as client:
        start_thread = threading.Thread.start
        monkeypatch.setattr(threading.Thread, "start", lambda thread: None if thread.name.startswith("healthguard-camera") else start_thread(thread))
        spec = {"elder_id": str(uuid4()), "user_id": str(uuid4()), "index": 0}
        started = client.post("/camera/start", json=spec, headers=HEADERS)
        assert started.status_code == 200
        access = {"elder_id": spec["elder_id"], "user_id": spec["user_id"], **started.json()}
        assert client.post("/camera/status", json={**access, "user_id": str(uuid4())}, headers=HEADERS).status_code == 404
        assert client.post("/camera/start", json=spec, headers=HEADERS).status_code == 409
        assert client.post("/camera/stop", json=access, headers=HEADERS).json()["status"] == "stopped"


def test_real_urfd_clip_detects_fall():
    sample = Path(__file__).resolve().parents[1] / "demo_videos/01_FALL_CONFIRMED_URFD_fall-19.mp4"
    with TestClient(create_app(KEY)) as client:
        result = client.post("/video?suffix=.mp4&profile=urfd", headers=HEADERS, content=sample.read_bytes())
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["event_ids"] and body["pose_frames"] > 0
        video = client.get(f"/video/{body['result_id']}", headers={**HEADERS, "Range": "bytes=0-99"})
        assert video.status_code == 206 and len(video.content) == 100


def test_live_pipeline_publishes_frame_and_forwards_confirmed_event(monkeypatch):
    delivered = threading.Event()
    released = threading.Event()
    rendered = threading.Event()
    posted = []
    state = SimpleNamespace(value="CONFIRMED_FALL")
    event = SimpleNamespace(event_id="FALL-0000000000001-ABCDEF01", metadata={"just_confirmed": True}, state=state)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    class Capture:
        def isOpened(self): return True
        def set(self, *_): return True
        def read(self): return True, frame
        def release(self): released.set()

    def forward(*args, **kwargs):
        posted.append(kwargs["json"])
        delivered.set()
        return SimpleNamespace(is_success=True)

    def encode(*args):
        rendered.set()
        return True, b"fake-jpeg"

    pipeline = SimpleNamespace(
        process_frame=lambda *_: SimpleNamespace(event=event),
        annotate_frame=lambda *_: frame,
        pose_estimator=SimpleNamespace(close=lambda: None),
        latest_event=event,
    )
    monkeypatch.setattr(healthguard_service, "build_pipeline", lambda _: pipeline)
    monkeypatch.setattr(healthguard_service.cv2, "VideoCapture", lambda _: Capture())
    monkeypatch.setattr(healthguard_service.cv2, "imencode", encode)
    monkeypatch.setattr(healthguard_service.httpx, "post", forward)
    with TestClient(create_app(KEY)) as client:
        spec = {"elder_id": str(uuid4()), "user_id": str(uuid4()), "index": 0, "notify": True}
        started = client.post("/camera/start", json=spec, headers=HEADERS)
        assert started.status_code == 200
        access = {**spec, **started.json()}
        assert delivered.wait(2) and rendered.wait(2)
        status = client.post("/camera/status", json=access, headers=HEADERS).json()
        assert status["status"] == "running" and status["state"] == "CONFIRMED_FALL" and status["jpeg"]
        assert client.post("/camera/stop", json=access, headers=HEADERS).json()["status"] == "stopped"
        assert released.wait(2)
        assert posted == [{key: spec[key] for key in ("elder_id", "user_id", "notify")} | {"event_id": event.event_id}]
