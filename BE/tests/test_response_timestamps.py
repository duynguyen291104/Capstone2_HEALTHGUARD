"""Response instants always include an offset; input timestamps remain strict."""

import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import DoseOccurrence, DoseResponse, DoseResponseType, DoseStatus
from app.schemas import DoseResponseCreate, InvitationPublic, ScheduleOut


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None
    return parsed


def test_response_normalizes_naive_utc_preserves_aware_and_local_calendar_fields():
    naive = datetime(2026, 10, 10, 12, 15)
    public = InvitationPublic(email="helper@example.com", care_group_name="Nhóm mẫu", expires_at=naive, valid=True)
    assert public.expires_at == naive.replace(tzinfo=UTC)
    assert timestamp(public.model_dump(mode="json")["expires_at"]) == naive.replace(tzinfo=UTC)
    local = datetime(2026, 10, 10, 19, 15, tzinfo=timezone(timedelta(hours=7)))
    aware = InvitationPublic(email="helper@example.com", care_group_name="Nhóm mẫu", expires_at=local, valid=True)
    assert aware.expires_at is local
    assert aware.model_dump(mode="json")["expires_at"].endswith("+07:00")
    schedule = ScheduleOut(
        id=uuid.uuid4(), elder_id=uuid.uuid4(), medication_id=uuid.uuid4(),
        medication_name="Thuốc mẫu", dose_amount="1", dose_unit="viên", instructions=None,
        start_date=date(2026, 10, 10), end_date=None, time_of_day=time(19, 15),
        days_of_week=[0], timezone="Asia/Ho_Chi_Minh", reminder_offsets_minutes=[0],
        escalation_after_minutes=60, assigned_caregiver_user_id=None, is_active=True,
    )
    dumped = schedule.model_dump(mode="json")
    assert dumped["start_date"] == "2026-10-10" and dumped["time_of_day"] == "19:15:00"
    with pytest.raises(ValidationError):
        DoseResponseCreate(status="ADMINISTERED", administered_at=naive)


async def test_sqlite_api_invites_and_nested_dose_history_return_explicit_utc(client, db_factory):
    user = await client.post("/api/v1/auth/register", json={
        "full_name": "Chủ nhóm", "email": "timestamps@example.com", "password": "safe-password-123",
    })
    await client.post("/api/v1/care-groups", json={"name": "Nhóm thời gian"})
    invitation = await client.post("/api/v1/care-groups/current/invitations", json={"email": "timestamp-helper@example.com"})
    public = await client.get(f"/api/v1/invitations/{invitation.json()['invitation_token']}")
    listing = await client.get("/api/v1/care-groups/current/invitations")
    assert timestamp(invitation.json()["expires_at"]) == timestamp(public.json()["expires_at"])
    assert timestamp(listing.json()[0]["expires_at"]) == timestamp(public.json()["expires_at"])
    timestamp(listing.json()[0]["created_at"])
    members = await client.get("/api/v1/care-groups/current/members")
    timestamp(members.json()[0]["joined_at"])
    elder = await client.post("/api/v1/elders", json={"full_name": "Bà mẫu"})
    timestamp(elder.json()["created_at"])
    timestamp(elder.json()["updated_at"])
    schedule = await client.post(f"/api/v1/elders/{elder.json()['id']}/medication-schedules", json={
        "medication_name": "Thuốc mẫu", "dose_amount": "1", "dose_unit": "viên",
        "start_date": "2026-10-10", "time_of_day": "19:15",
    })
    expected = datetime(2026, 10, 10, 12, 15, tzinfo=UTC)
    async with db_factory() as db:
        occurrence = DoseOccurrence(
            schedule_id=uuid.UUID(schedule.json()["id"]), scheduled_for=expected,
            status=DoseStatus.ADMINISTERED,
        )
        db.add(occurrence)
        await db.flush()
        db.add(DoseResponse(
            occurrence_id=occurrence.id, responded_by_user_id=uuid.UUID(user.json()["user"]["id"]),
            response_type=DoseResponseType.ADMINISTERED, administered_at=expected,
            responded_at=expected + timedelta(minutes=1),
        ))
        await db.commit()
    doses = await client.get("/api/v1/dose-occurrences?date=2026-10-10")
    assert doses.status_code == 200 and len(doses.json()) == 1
    dose = doses.json()[0]
    assert timestamp(dose["scheduled_for"]) == expected
    assert timestamp(dose["response"]["administered_at"]) == expected
    assert timestamp(dose["response"]["responded_at"]) == expected + timedelta(minutes=1)
