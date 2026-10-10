"""Opt-in local PostgreSQL concurrency checks in an isolated random schema.

Run from BE: .venv\\Scripts\\python.exe scripts\\check_postgres_concurrency.py --run
Requires CREATE SCHEMA permission. Never reads/writes application tables in
public: every test connection has search_path ONLY the generated schema, and
schema_translate_map explicitly qualifies every ORM table. Only that exact
randomly generated test namespace is removed afterward. No real Telegram sends.
Connection credentials, JWT secrets and Telegram tokens are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import sys
import uuid
from zoneinfo import ZoneInfo

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_ROOT))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def checks(factory, app, schema: str) -> list[str]:
    from app.models import CareGroupMember, DoseOccurrence, DoseResponse, DoseStatus, MedicationSchedule, NotificationAttempt, User
    from app.services import scheduler
    from app.services.telegram import TelegramResult

    sent = []
    async def fake_send(**kwargs):
        sent.append(kwargs)
        await asyncio.sleep(.05)  # Make simultaneous dispatchers genuinely overlap.
        return TelegramResult(True, message_id="isolated-provider-test")
    previous_send = scheduler.telegram_client.send_message
    scheduler.telegram_client.send_message = fake_send
    completed = []
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://healthguard-test.local") as owner, AsyncClient(transport=transport, base_url="http://healthguard-test.local") as helper:
            async def post(client, path, payload, headers=None):
                response = await client.post(path, json=payload, headers=headers)
                require(response.status_code in {200, 201}, f"Fixture setup rejected: {path} ({response.status_code})")
                return response.json()
            owner_auth = await post(owner, "/api/v1/auth/register", {"full_name": "Isolated owner", "email": "owner@concurrency.example.com", "password": "isolated-test-password"})
            group = await post(owner, "/api/v1/care-groups", {"name": "Isolated concurrency check"})
            headers = {"X-Care-Group-ID": group["id"]}
            helper_auth = await post(helper, "/api/v1/auth/register", {"full_name": "Isolated caregiver", "email": "caregiver@concurrency.example.com", "password": "isolated-test-password"})
            invitation = await post(owner, "/api/v1/care-groups/current/invitations", {"email": "caregiver@concurrency.example.com"}, headers)
            accept_path = f"/api/v1/invitations/{invitation['invitation_token']}/accept"
            accepted = await asyncio.gather(*(helper.post(accept_path) for _ in range(5)))
            require(sorted(response.status_code for response in accepted) == [200, 409, 409, 409, 409], "Parallel invitation acceptance did not have exactly one winner")
            async with factory() as db:
                require(await db.scalar(select(func.count()).select_from(CareGroupMember).where(CareGroupMember.user_id == uuid.UUID(helper_auth["user"]["id"]))) == 1, "Duplicate caregiver membership")
            completed.append("Parallel invitation acceptance: 1 success / 4 conflicts / 1 membership")

            elder = await post(owner, "/api/v1/elders", {"full_name": "Fictional concurrency elder"}, headers)
            await helper.post("/api/v1/auth/logout")  # Caregiver session unnecessary for owner fixtures.
            now = datetime.now(UTC).replace(microsecond=0)
            timezone = ZoneInfo("Asia/Ho_Chi_Minh")
            local_due = (now - timedelta(minutes=5)).astimezone(timezone)
            payload = {"medication_name": "Fictional demonstration 10mg", "dose_amount": "1", "dose_unit": "vien", "start_date": local_due.date().isoformat(), "time_of_day": local_due.time().replace(tzinfo=None).isoformat(), "timezone": "Asia/Ho_Chi_Minh"}
            keyed = {**headers, "Idempotency-Key": str(uuid.uuid4())}
            create_path = f"/api/v1/elders/{elder['id']}/medication-schedules"
            created = await asyncio.gather(*(owner.post(create_path, headers=keyed, json=payload) for _ in range(6)))
            require(all(response.status_code == 201 for response in created), "Concurrent import retry was not acknowledged")
            ids = {response.json()["id"] for response in created}
            require(len(ids) == 1, "Concurrent import retry created different schedules")
            schedule_id = uuid.UUID(ids.pop())
            async with factory() as db:
                require(await db.scalar(select(func.count()).select_from(MedicationSchedule)) == 1, "Concurrent import created duplicate schedule rows")
                user = await db.get(User, uuid.UUID(owner_auth["user"]["id"]))
                user.telegram_chat_id = "111111"  # Fake; fake_send intercepts all provider calls.
                await db.commit()
            completed.append("Concurrent import retries: 6 acknowledgements / 1 schedule")

            async def worker_part(operation):
                async with factory() as db:
                    require(await db.scalar(text("SELECT current_schema()")) == schema, "Worker connection escaped test schema")
                    return await operation(db, now)
            generated = await asyncio.gather(*(worker_part(scheduler.ensure_occurrences) for _ in range(5)))
            async with factory() as db:
                occurrences = (await db.scalars(select(DoseOccurrence))).all()
                require(bool(occurrences) and sum(generated) == len(occurrences), "Worker occurrence deduplication failed")
                require(len({(item.schedule_id, item.scheduled_for) for item in occurrences}) == len(occurrences), "Duplicate occurrence times")
            processed = await asyncio.gather(*(worker_part(scheduler.process_due_occurrences) for _ in range(5)))
            require(sum(processed) == 1, "Concurrent worker processed a due dose multiple times")
            async with factory() as db:
                require(await db.scalar(select(func.count()).select_from(NotificationAttempt)) == 1, "Duplicate queued notification")
                due = await db.scalar(select(DoseOccurrence).where(DoseOccurrence.status == DoseStatus.DUE))
                due_id = due.id
            dispatched = await asyncio.gather(*(worker_part(scheduler.dispatch_notifications) for _ in range(5)))
            require(sum(dispatched) == 1 and len(sent) == 1, "Concurrent dispatch sent a fake message more than once")
            completed.append("5 parallel workers: unique occurrences / 1 due transition / 1 queued and fake-delivered notification")

            response_path = f"/api/v1/dose-occurrences/{due_id}/responses"
            answers = await asyncio.gather(*(owner.post(response_path, headers=headers, json={"status": "ADMINISTERED", "note": "Same retry"}) for _ in range(5)))
            require(all(response.status_code == 200 for response in answers), "Concurrent same-payload responses not idempotent")
            async with factory() as db:
                require(await db.scalar(select(func.count()).select_from(DoseResponse)) == 1, "Duplicate dose responses")
                other = DoseOccurrence(schedule_id=schedule_id, scheduled_for=now - timedelta(minutes=6), status=DoseStatus.DUE, next_action_at=now)
                db.add(other)
                await db.commit()
                other_id = other.id
            other_path = f"/api/v1/dose-occurrences/{other_id}/responses"
            conflicting = await asyncio.gather(owner.post(other_path, headers=headers, json={"status": "ADMINISTERED", "note": "Conflicting A"}), owner.post(other_path, headers=headers, json={"status": "CANNOT_ADMINISTER", "reason_code": "refused", "note": "Conflicting B"}))
            require(sorted(response.status_code for response in conflicting) == [200, 409], "Conflicting responses did not have exactly one winner")
            async with factory() as db:
                require(await db.scalar(select(func.count()).select_from(DoseResponse).where(DoseResponse.occurrence_id == other_id)) == 1, "Conflicting response duplicate")
            completed.append("Parallel dose responses: 5 identical retries -> 1 response; conflicting answers -> 1 success / 1 conflict")
    finally:
        scheduler.telegram_client.send_message = previous_send
    return completed


async def run() -> int:
    os.chdir(BE_ROOT)
    from app.config import Settings, get_settings
    configured = Settings()
    url = make_url(configured.database_url)
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        print("SKIPPED: configured database is not a local PostgreSQL server.")
        return 2
    # Establish safe test settings before importing app/database. No cookie/JWT
    # from the real application is used and no provider token is used.
    os.environ.update({"ENVIRONMENT": "test", "JWT_SECRET": "isolated-postgres-concurrency-secret-32-plus", "COOKIE_SECURE": "false", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_WEBHOOK_SECRET": "", "TELEGRAM_BOT_USERNAME": ""})
    get_settings.cache_clear()
    schema = "healthguard_test_" + uuid.uuid4().hex
    require(re.fullmatch(r"healthguard_test_[0-9a-f]{32}", schema) is not None, "Invalid generated namespace")
    # A nonexistent search_path is fine until CREATE SCHEMA. Never include
    # public: create_all/has_table must not accidentally reuse real tables.
    raw_engine = create_async_engine(url.set(drivername="postgresql+asyncpg"), echo=False, connect_args={"timeout": 5, "server_settings": {"search_path": schema, "statement_timeout": "15000", "lock_timeout": "10000"}}, pool_pre_ping=True)
    engine = raw_engine.execution_options(schema_translate_map={None: schema})
    created_schema = False
    original_overrides = None
    app = None
    outcome = 1
    stage = "create isolated schema"
    try:
        async with raw_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION CURRENT_USER'))
        created_schema = True
        stage = "create isolated tables"
        from app.database import Base, get_db
        from app.main import app
        async with engine.begin() as connection:
            require(await connection.scalar(text("SELECT current_schema()")) == schema, "Search path isolation failed")
            require((await connection.scalar(text("SHOW search_path"))).strip('"') == schema, "Unexpected search path")
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async def isolated_db():
            async with factory() as db:
                yield db
        original_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = isolated_db
        stage = "run isolated concurrency scenarios"
        async with asyncio.timeout(120):
            completed = await checks(factory, app, schema)
        for line in completed:
            print("PASS: " + line)
        outcome = 0
    except Exception as error:
        # Driver/HTTP error repr can expose DSN or bearer tokens. Print only
        # controlled assertion messages or exception class, never raw errors.
        if isinstance(error, AssertionError):
            print("FAILED: " + str(error))
        else:
            original = getattr(error, "orig", None)
            code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
            suffix = f" / SQLSTATE {code}" if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code) else ""
            print(f"UNAVAILABLE/FAILED at {stage}: {type(error).__name__}{suffix}.")
            if code == "42501":
                print("The configured role lacks CREATE SCHEMA permission; no permission change was attempted.")
    finally:
        if app is not None and original_overrides is not None:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)
        if created_schema:
            try:
                require(re.fullmatch(r"healthguard_test_[0-9a-f]{32}", schema) is not None, "Invalid cleanup namespace")
                async with raw_engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                print("CLEANUP: exact generated test schema removed; application tables untouched.")
            except Exception as error:
                print(f"CLEANUP FAILED: {type(error).__name__}; isolated schema retained: {schema}")
                outcome = 1
        await raw_engine.dispose()
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly permit creating/removing one generated test schema on configured LOCAL PostgreSQL.")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 2
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
