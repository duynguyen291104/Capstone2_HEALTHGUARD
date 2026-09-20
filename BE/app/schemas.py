from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import DoseResponseType, DoseStatus, GroupRole, MobilityLevel, Sex


Name = Annotated[str, Field(min_length=1, max_length=150)]
Password = Annotated[str, Field(min_length=10, max_length=128)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OwnerRegister(BaseModel):
    full_name: Name
    email: EmailStr
    password: Password
    phone: str | None = Field(default=None, max_length=30)
    care_group_name: Name


class CaregiverRegister(BaseModel):
    full_name: Name
    email: EmailStr
    password: Password
    phone: str | None = Field(default=None, max_length=30)
    invitation_token: str = Field(min_length=32, max_length=300)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TelegramLinkRequest(BaseModel):
    link_code: str = Field(min_length=16, max_length=200)


class TelegramLinkOut(BaseModel):
    deep_link: str
    expires_at: datetime


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: str | None
    telegram_linked: bool = False


class GroupSummary(ORMModel):
    id: uuid.UUID
    name: str
    role: GroupRole


class AuthOut(BaseModel):
    user: UserOut
    groups: list[GroupSummary]
    default_group_id: uuid.UUID | None


class MessageOut(BaseModel):
    message: str


class MemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: EmailStr
    role: GroupRole
    joined_at: datetime


class InvitationCreate(BaseModel):
    email: EmailStr
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class InvitationCreated(BaseModel):
    id: uuid.UUID
    email: EmailStr
    expires_at: datetime
    invitation_token: str
    invitation_url: str


class InvitationPublic(BaseModel):
    email: EmailStr
    care_group_name: str
    expires_at: datetime
    valid: bool


class InvitationOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    role: GroupRole
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ElderCreate(BaseModel):
    full_name: Name
    date_of_birth: date | None = None
    sex: Sex = Sex.UNDISCLOSED
    height_cm: Decimal | None = Field(default=None, gt=0, le=300, max_digits=5, decimal_places=2)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=1000, max_digits=5, decimal_places=2)
    diagnosed_conditions: list[str] = Field(default_factory=list, max_length=100)
    current_medications_note: str | None = Field(default=None, max_length=3000)
    mobility_level: MobilityLevel | None = None
    sleep_habits: str | None = Field(default=None, max_length=2000)
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)

    @field_validator("diagnosed_conditions")
    @classmethod
    def clean_conditions(cls, value: list[str]) -> list[str]:
        result = [item.strip() for item in value if item.strip()]
        if any(len(item) > 200 for item in result):
            raise ValueError("Each diagnosed condition must be at most 200 characters")
        return list(dict.fromkeys(result))


class ElderUpdate(BaseModel):
    full_name: Name | None = None
    date_of_birth: date | None = None
    sex: Sex | None = None
    height_cm: Decimal | None = Field(default=None, gt=0, le=300, max_digits=5, decimal_places=2)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=1000, max_digits=5, decimal_places=2)
    diagnosed_conditions: list[str] | None = Field(default=None, max_length=100)
    current_medications_note: str | None = Field(default=None, max_length=3000)
    mobility_level: MobilityLevel | None = None
    sleep_habits: str | None = Field(default=None, max_length=2000)
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    is_active: bool | None = None

    @field_validator("diagnosed_conditions")
    @classmethod
    def clean_conditions(cls, value: list[str] | None) -> list[str] | None:
        return ElderCreate.clean_conditions(value) if value is not None else None


class ElderOut(ORMModel):
    id: uuid.UUID
    group_id: uuid.UUID
    full_name: str
    date_of_birth: date | None
    sex: Sex
    height_cm: Decimal | None
    weight_kg: Decimal | None
    diagnosed_conditions: list[str] | None
    current_medications_note: str | None
    mobility_level: MobilityLevel | None
    sleep_habits: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AssignmentUpsert(BaseModel):
    can_view_medications: bool = True
    can_confirm_doses: bool = True
    can_view_diagnoses: bool = False


class AssignmentOut(ORMModel):
    elder_id: uuid.UUID
    caregiver_user_id: uuid.UUID
    can_view_medications: bool
    can_confirm_doses: bool
    can_view_diagnoses: bool
    created_at: datetime


class ScheduleCreate(BaseModel):
    medication_name: str = Field(min_length=1, max_length=200)
    dose_amount: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    dose_unit: str = Field(min_length=1, max_length=50)
    instructions: str | None = Field(default=None, max_length=3000)
    start_date: date
    end_date: date | None = None
    time_of_day: time
    days_of_week: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    timezone: str = "Asia/Ho_Chi_Minh"
    reminder_offsets_minutes: list[int] = Field(default_factory=lambda: [0, 15, 30, 45])
    escalation_after_minutes: int = Field(default=60, ge=1, le=1440)
    assigned_caregiver_user_id: uuid.UUID | None = None

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("days_of_week values must be between 0 (Monday) and 6 (Sunday)")
        return sorted(set(value))

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def validate_offsets(cls, value: list[int]) -> list[int]:
        normalized = sorted(set(value))
        if not normalized or normalized[0] != 0:
            raise ValueError("reminder_offsets_minutes must begin with 0")
        if len(normalized) > 10 or any(item < 0 or item > 1440 for item in normalized):
            raise ValueError("Invalid reminder offsets")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_dates_and_escalation(self) -> ScheduleCreate:
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if self.escalation_after_minutes <= self.reminder_offsets_minutes[-1]:
            raise ValueError("escalation must occur after the final reminder")
        return self


class ScheduleUpdate(BaseModel):
    medication_name: str | None = Field(default=None, min_length=1, max_length=200)
    dose_amount: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    dose_unit: str | None = Field(default=None, min_length=1, max_length=50)
    instructions: str | None = Field(default=None, max_length=3000)
    end_date: date | None = None
    time_of_day: time | None = None
    days_of_week: list[int] | None = Field(default=None, min_length=1, max_length=7)
    timezone: str | None = None
    reminder_offsets_minutes: list[int] | None = None
    escalation_after_minutes: int | None = Field(default=None, ge=1, le=1440)
    assigned_caregiver_user_id: uuid.UUID | None = None
    is_active: bool | None = None

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, value: list[int] | None) -> list[int] | None:
        return ScheduleCreate.validate_days(value) if value is not None else None

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def validate_offsets(cls, value: list[int] | None) -> list[int] | None:
        return ScheduleCreate.validate_offsets(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return ScheduleCreate.validate_timezone(value) if value is not None else None


class ScheduleOut(BaseModel):
    id: uuid.UUID
    elder_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dose_amount: Decimal
    dose_unit: str
    instructions: str | None
    start_date: date
    end_date: date | None
    time_of_day: time
    days_of_week: list[int]
    timezone: str
    reminder_offsets_minutes: list[int]
    escalation_after_minutes: int
    assigned_caregiver_user_id: uuid.UUID | None
    is_active: bool


class DoseResponseCreate(BaseModel):
    status: DoseResponseType
    reason_code: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    administered_at: datetime | None = None

    @model_validator(mode="after")
    def validate_response(self) -> DoseResponseCreate:
        if self.status == DoseResponseType.CANNOT_ADMINISTER and not self.reason_code:
            raise ValueError("reason_code is required when medicine cannot be administered")
        if self.status == DoseResponseType.ADMINISTERED and self.reason_code:
            raise ValueError("reason_code is only allowed when medicine cannot be administered")
        if self.administered_at and self.administered_at.tzinfo is None:
            raise ValueError("administered_at must include a timezone")
        return self


class DoseResponseOut(ORMModel):
    id: uuid.UUID
    responded_by_user_id: uuid.UUID
    response_type: DoseResponseType
    administered_at: datetime | None
    reason: str | None
    notes: str | None
    responded_at: datetime


class DoseOccurrenceOut(BaseModel):
    id: uuid.UUID
    elder_id: uuid.UUID
    elder_name: str
    schedule_id: uuid.UUID
    medication_name: str
    dose_amount: Decimal
    dose_unit: str
    instructions: str | None
    scheduled_for: datetime
    status: DoseStatus
    reminder_count: int
    response: DoseResponseOut | None
