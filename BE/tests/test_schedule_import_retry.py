"""A lost import response must not result in duplicate medicine reminders."""
import uuid

from sqlalchemy import func, select

from app.models import AuditLog, Medication, MedicationSchedule


async def test_import_retry_acknowledges_same_schedule_and_rejects_changed_payload(client, db_factory):
    register = await client.post("/api/v1/auth/register", json={
        "full_name": "OCR Retry Test", "email": "ocr-retry@example.com",
        "password": "test-import-password",
    })
    assert register.status_code == 201
    group = await client.post("/api/v1/care-groups", json={"name": "Import test"})
    headers = {"X-Care-Group-ID": group.json()["id"], "Idempotency-Key": str(uuid.uuid4())}
    elder = await client.post("/api/v1/elders", headers=headers, json={"full_name": "Fictional elder"})
    url = f"/api/v1/elders/{elder.json()['id']}/medication-schedules"
    payload = {
        "medication_name": "Demonstration A", "dose_amount": "1", "dose_unit": "vien",
        "start_date": "2026-10-04", "time_of_day": "08:00:00",
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
    }
    first = await client.post(url, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    retry = await client.post(url, headers=headers, json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == first.json()["id"]
    changed = await client.post(url, headers=headers, json={**payload, "dose_amount": "2"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IMPORT_ALREADY_SAVED"
    other_elder = await client.post("/api/v1/elders", headers=headers, json={"full_name": "Other fictional elder"})
    reused = await client.post(f"/api/v1/elders/{other_elder.json()['id']}/medication-schedules", headers=headers, json=payload)
    assert reused.status_code == 409
    invalid = await client.post(url, headers={**headers, "Idempotency-Key": "invalid"}, json=payload)
    assert invalid.status_code == 400
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MedicationSchedule)) == 1
        assert await db.scalar(select(func.count()).select_from(Medication)) == 1
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "MEDICATION_SCHEDULE_CREATED"))
        assert audit.details["idempotency_key"] == headers["Idempotency-Key"]


async def test_schedule_creation_without_import_key_keeps_existing_behavior(client):
    await client.post("/api/v1/auth/register", json={
        "full_name": "Regular Schedule Test", "email": "schedule-test@example.com",
        "password": "test-import-password",
    })
    group = await client.post("/api/v1/care-groups", json={"name": "Regular test"})
    headers = {"X-Care-Group-ID": group.json()["id"]}
    elder = await client.post("/api/v1/elders", headers=headers, json={"full_name": "Fictional elder"})
    result = await client.post(f"/api/v1/elders/{elder.json()['id']}/medication-schedules", headers=headers, json={
        "medication_name": "Demonstration B", "dose_amount": "1", "dose_unit": "vien",
        "start_date": "2026-10-04", "time_of_day": "08:00:00",
    })
    assert result.status_code == 201, result.text
