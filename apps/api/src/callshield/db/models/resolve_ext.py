"""Resolve extension tables (new; the base resolve tables are shared and untouched).

All append-only in spirit: corrections are new rows, never edits of evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, created_column, id_column


class ContactAttemptDetail(TenantOwned, Base):
    """1:1 extension of contact_attempts: provenance of the attempt and safety metadata.

    `authorization_id` is UNIQUE: one authorization can back at most one attempt
    (single use enforced in the schema, not just in code).
    """

    __tablename__ = "contact_attempt_details"

    id: Mapped[uuid.UUID] = id_column()
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contact_attempts.id"), unique=True, nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    source: Mapped[str] = mapped_column(
        String(24), nullable=False
    )  # SYSTEM | USER_MANUAL | USER_HISTORIC
    evidence_label: Mapped[str] = mapped_column(String(32), nullable=False)
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contact_authorizations.id"), unique=True
    )
    dialed_phone_masked: Mapped[str] = mapped_column(String(32), default="")
    policy_version: Mapped[str] = mapped_column(String(64), default="")
    template_version: Mapped[str] = mapped_column(String(64), default="")
    dialer_kind: Mapped[str] = mapped_column(String(32), default="")
    stop_condition: Mapped[str | None] = mapped_column(String(48))
    parked_for_review: Mapped[bool] = mapped_column(Boolean, default=False)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_column()


class AttemptTranscriptLine(TenantOwned, Base):
    """Transcript text exactly as heard/spoken. Counterparty lines are CLAIMED."""

    __tablename__ = "attempt_transcript_lines"
    __table_args__ = (UniqueConstraint("attempt_id", "seq"),)

    id: Mapped[uuid.UUID] = id_column()
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contact_attempts.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # COUNTERPARTY | AGENT | SYSTEM
    text: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)
    offset_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = created_column()


class ResolutionTimelineEntry(TenantOwned, Base):
    """R-008 persistent case timeline (append-only)."""

    __tablename__ = "resolution_timeline_entries"

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contact_attempts.id"))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = created_column()


class ResolutionNextStep(TenantOwned, Base):
    """R-009 recommended next step. A CALL suggestion never carries authorization."""

    __tablename__ = "resolution_next_steps"
    __table_args__ = (UniqueConstraint("case_id", "attempt_id", "kind"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contact_attempts.id"))
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    involves_call: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_new_authorization: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="SUGGESTED")
    created_at: Mapped[datetime] = created_column()


class RecontactProposal(TenantOwned, Base):
    """R-010: a later inbound incident proposed for attribution review. Never auto-reopens."""

    __tablename__ = "recontact_proposals"
    __table_args__ = (UniqueConstraint("case_id", "incident_id"),)

    id: Mapped[uuid.UUID] = id_column()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="PROPOSED"
    )  # PROPOSED|RELATED|NOT_RELATED
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_column()
