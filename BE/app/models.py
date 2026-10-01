from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GroupRole(str, enum.Enum):
    OWNER = "OWNER"
    CAREGIVER = "CAREGIVER"


class Sex(str, enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNDISCLOSED = "UNDISCLOSED"


class MobilityLevel(str, enum.Enum):
    INDEPENDENT = "INDEPENDENT"
    NEEDS_ASSISTANCE = "NEEDS_ASSISTANCE"
    WHEELCHAIR = "WHEELCHAIR"
    BEDRIDDEN = "BEDRIDDEN"


class DoseStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    DUE = "DUE"
    ADMINISTERED = "ADMINISTERED"
    CANNOT_ADMINISTER = "CANNOT_ADMINISTER"
    UNCONFIRMED = "UNCONFIRMED"
    CANCELLED = "CANCELLED"


class DoseResponseType(str, enum.Enum):
    ADMINISTERED = "ADMINISTERED"
    CANNOT_ADMINISTER = "CANNOT_ADMINISTER"


class NotificationKind(str, enum.Enum):
    REMINDER = "REMINDER"
    ESCALATION = "ESCALATION"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    memberships: Mapped[list[CareGroupMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TelegramLinkCode(Base):
    __tablename__ = "telegram_link_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship()


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class CareGroup(TimestampMixin, Base):
    __tablename__ = "care_groups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    creator: Mapped[User] = relationship(foreign_keys=[created_by_user_id])
    members: Mapped[list[CareGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    elders: Mapped[list[ElderProfile]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class CareGroupMember(Base):
    __tablename__ = "care_group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("care_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[GroupRole] = mapped_column(
        Enum(GroupRole, native_enum=False, length=20), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group: Mapped[CareGroup] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (Index("ix_invitation_group_email", "group_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("care_groups.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[GroupRole] = mapped_column(
        Enum(GroupRole, native_enum=False, length=20), default=GroupRole.CAREGIVER, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group: Mapped[CareGroup] = relationship()
    invited_by: Mapped[User] = relationship()


class ElderProfile(TimestampMixin, Base):
    __tablename__ = "elder_profiles"
    __table_args__ = (
        CheckConstraint("height_cm IS NULL OR height_cm > 0", name="ck_elder_height_positive"),
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="ck_elder_weight_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("care_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[Sex] = mapped_column(
        Enum(Sex, native_enum=False, length=20), default=Sex.UNDISCLOSED, nullable=False
    )
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    diagnoses: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    current_medications_note: Mapped[str | None] = mapped_column(Text)
    mobility_level: Mapped[MobilityLevel | None] = mapped_column(
        Enum(MobilityLevel, native_enum=False, length=30)
    )
    sleep_habits: Mapped[str | None] = mapped_column(Text)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    group: Mapped[CareGroup] = relationship(back_populates="elders")
    assignments: Mapped[list[CaregiverAssignment]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )
    medications: Mapped[list[Medication]] = relationship(
        back_populates="elder", cascade="all, delete-orphan"
    )


class CaregiverAssignment(Base):
    __tablename__ = "caregiver_assignments"
    __table_args__ = (
        UniqueConstraint("elder_id", "caregiver_user_id", name="uq_elder_caregiver"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    caregiver_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    can_view_medications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_confirm_doses: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_view_diagnoses: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    elder: Mapped[ElderProfile] = relationship(back_populates="assignments")
    caregiver: Mapped[User] = relationship(foreign_keys=[caregiver_user_id])
    assigned_by: Mapped[User] = relationship(foreign_keys=[assigned_by_user_id])


class Medication(TimestampMixin, Base):
    __tablename__ = "medications"
    __table_args__ = (
        CheckConstraint("dose_amount > 0", name="ck_medication_dose_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elder_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dose_amount: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    dose_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    elder: Mapped[ElderProfile] = relationship(back_populates="medications")
    schedules: Mapped[list[MedicationSchedule]] = relationship(
        back_populates="medication", cascade="all, delete-orphan"
    )


class MedicationSchedule(TimestampMixin, Base):
    __tablename__ = "medication_schedules"
    __table_args__ = (
        CheckConstraint("escalation_after_minutes > 0", name="ck_schedule_escalation_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    time_of_day: Mapped[time] = mapped_column(Time, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, default=lambda: list(range(7)), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Ho_Chi_Minh", nullable=False)
    reminder_offsets_minutes: Mapped[list[int]] = mapped_column(
        JSON, default=lambda: [0, 15, 30, 45], nullable=False
    )
    escalation_after_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    assigned_caregiver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    medication: Mapped[Medication] = relationship(back_populates="schedules")
    assigned_caregiver: Mapped[User | None] = relationship()
    occurrences: Mapped[list[DoseOccurrence]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class DoseOccurrence(Base):
    __tablename__ = "dose_occurrences"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_occurrence_time"),
        Index("ix_dose_due", "status", "next_action_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medication_schedules.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[DoseStatus] = mapped_column(
        Enum(DoseStatus, native_enum=False, length=30), default=DoseStatus.SCHEDULED, nullable=False
    )
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    schedule: Mapped[MedicationSchedule] = relationship(back_populates="occurrences")
    response: Mapped[DoseResponse | None] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan", uselist=False
    )


class DoseResponse(Base):
    __tablename__ = "dose_responses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dose_occurrences.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    responded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    response_type: Mapped[DoseResponseType] = mapped_column(
        Enum(DoseResponseType, native_enum=False, length=30), nullable=False
    )
    administered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    occurrence: Mapped[DoseOccurrence] = relationship(back_populates="response")
    responded_by: Mapped[User] = relationship()


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (
        UniqueConstraint(
            "occurrence_id", "recipient_user_id", "kind", "ordinal", name="uq_notification_attempt"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dose_occurrences.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(NotificationKind, native_enum=False, length=20), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(String(500))
    delivery_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    occurrence: Mapped[DoseOccurrence] = relationship()
    recipient: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("care_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group: Mapped[CareGroup] = relationship()
    actor: Mapped[User | None] = relationship()
