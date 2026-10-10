"""Loopback-only UI review with REAL APIs and isolated in-memory test data.

Run from repo root: BE\\.venv\\Scripts\\python.exe BE\\scripts\\run_ui_review.py
No production database, worker, tunnel or outbound Telegram is started.
The three demonstration logins share the fictional password Review-demo-2026.
owner@review.example.com / caregiver@review.example.com / viewer@review.example.com
"""
import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT / "BE")
sys.path.insert(0, str(ROOT / "BE"))
os.environ.update({
    "ENVIRONMENT": "test", "DATABASE_URL": "sqlite+aiosqlite://",
    "JWT_SECRET": "isolated-ui-review-not-a-production-secret-2026",
    "COOKIE_NAME": "healthguard_review_token", "COOKIE_SECURE": "false",
    "CORS_ORIGINS": '["http://127.0.0.1:3014"]',
    "FRONTEND_BASE_URL": "http://127.0.0.1:3014",
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_BOT_USERNAME": "", "TELEGRAM_WEBHOOK_SECRET": "",
})

import uvicorn  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (CareGroup, CareGroupMember, CaregiverAssignment, DoseOccurrence,
                        DoseStatus, ElderProfile, GroupRole, Medication, MedicationSchedule, User)  # noqa: E402
from app.security import hash_password  # noqa: E402


@app.middleware("http")
async def review_request_status(request, call_next):
    response = await call_next(request)
    if response.status_code >= 400:
        # Route and status only: never log cookies, invitation tokens or bodies.
        print(f"REVIEW {request.method} {request.url.path}: {response.status_code}", flush=True)
    return response


async def prepare_database():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        password_hash = hash_password("Review-demo-2026")
        users = [User(id=uuid4(), email=f"{role}@review.example.com", full_name=name,
                      password_hash=password_hash) for role, name in (
            ("owner", "Chủ nhóm kiểm thử"), ("caregiver", "Người chăm sóc kiểm thử"),
            ("viewer", "Người chỉ xem hồ sơ"),
        )]
        db.add_all(users)
        await db.flush()
        group = CareGroup(id=uuid4(), name="Nhóm kiểm thử — dữ liệu giả lập", created_by_user_id=users[0].id)
        db.add(group)
        await db.flush()
        db.add_all([CareGroupMember(group_id=group.id, user_id=user.id,
                                   role=GroupRole.OWNER if index == 0 else GroupRole.CAREGIVER)
                    for index, user in enumerate(users)])
        elder = ElderProfile(id=uuid4(), group_id=group.id, full_name="Hồ sơ A — giả lập",
                             diagnoses=["Thông tin chẩn đoán kiểm thử"],
                             current_medications_note="Ghi chú thuốc — chỉ người có quyền thuốc được xem")
        other = ElderProfile(id=uuid4(), group_id=group.id, full_name="Hồ sơ B — chưa phân công")
        db.add_all([elder, other])
        await db.flush()
        db.add_all([CaregiverAssignment(elder_id=elder.id, caregiver_user_id=users[1].id,
                                       assigned_by_user_id=users[0].id,
                                       can_view_medications=True, can_confirm_doses=True, can_view_diagnoses=False),
                    CaregiverAssignment(elder_id=elder.id, caregiver_user_id=users[2].id,
                                       assigned_by_user_id=users[0].id,
                                       can_view_medications=False, can_confirm_doses=False, can_view_diagnoses=False)])
        now = datetime.now(UTC).replace(microsecond=0)
        for index, delta in enumerate((-5, 30)):
            scheduled = now + timedelta(minutes=delta)
            medicine = Medication(id=uuid4(), elder_id=elder.id,
                                  name=f"Thuốc giả lập {index + 1}", dose_amount=1, dose_unit="viên",
                                  instructions="Dữ liệu kiểm thử, không dùng để điều trị",
                                  start_date=(now - timedelta(days=1)).date(),
                                  created_by_user_id=users[0].id)
            db.add(medicine)
            await db.flush()
            schedule = MedicationSchedule(id=uuid4(), medication_id=medicine.id,
                                          time_of_day=scheduled.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).time().replace(tzinfo=None),
                                          assigned_caregiver_user_id=users[1].id)
            db.add(schedule)
            await db.flush()
            db.add(DoseOccurrence(schedule_id=schedule.id, scheduled_for=scheduled,
                                  status=DoseStatus.DUE if delta < 0 else DoseStatus.SCHEDULED,
                                  next_action_at=None))
        await db.commit()

    async def review_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = review_db
    return engine


def require_unused(port):
    with socket.socket() as connection:
        try:
            connection.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Review port {port} is already in use; no process was stopped.") from None


async def main():
    for port in (3014, 8014):
        require_unused(port)
    node = shutil.which("node") or (str(Path("E:/nodejs/node.exe")) if Path("E:/nodejs/node.exe").is_file() else None)
    if not node:
        raise SystemExit("Node.js is required for UI review")
    engine = await prepare_database()
    env = {**os.environ, "NEXT_PUBLIC_API_URL": "http://127.0.0.1:8014/api/v1"}
    frontend = subprocess.Popen([node, str(ROOT / "FE/node_modules/next/dist/bin/next"), "dev",
                                 "--port", "3014", "--hostname", "127.0.0.1"], cwd=ROOT / "FE", env=env,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    print("Isolated UI review: http://127.0.0.1:3014 — no real data or Telegram.", flush=True)
    try:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8014, log_level="warning"))
        await server.serve()
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(frontend.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        elif frontend.poll() is None:
            frontend.terminate()
        await engine.dispose()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
