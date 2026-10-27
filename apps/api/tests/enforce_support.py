"""Shared builders and fixtures for ENFORCE-core tests (fictional numbers only)."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import callshield.casebuilder  # noqa: F401  (registers extension tables)
from callshield.core.config import Settings
from callshield.db.models import (
    CallSession,
    ConsentRecord,
    Incident,
    OrganizationClaim,
    ProtectedLine,
)
from callshield.db.models.resolve import (
    ContactAttempt,
    ContactDestination,
    OptOutEvent,
    ResolutionCase,
)
from callshield.domain.enums import LineVerificationStatus, ProcessingMode
from callshield.evidence.store import LocalArtifactStore, set_artifact_store

LINE_NUMBER = "+14155550123"
CALLER_A = "+14155550177"
CALLER_B = "+14155550188"
BASE_TIME = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
_counter = itertools.count(1)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LocalArtifactStore]:
    artifact_store = LocalArtifactStore(tmp_path / "evidence")
    set_artifact_store(artifact_store)
    yield artifact_store
    set_artifact_store(None)


@pytest.fixture
def app_settings() -> Settings:
    return Settings(db_url="sqlite://", environment="test")


def seed_line(
    session: Session,
    tenant_id: uuid.UUID,
    e164: str = LINE_NUMBER,
    owner: uuid.UUID | None = None,
    verified: bool = True,
) -> ProtectedLine:
    line = ProtectedLine(
        tenant_id=tenant_id,
        e164=e164,
        label="Test line",
        owner_user_id=owner,
        verification_status=(
            LineVerificationStatus.VERIFIED.value
            if verified
            else LineVerificationStatus.UNVERIFIED.value
        ),
    )
    session.add(line)
    session.flush()
    return line


def seed_incident(
    session: Session,
    tenant_id: uuid.UUID,
    line_id: uuid.UUID,
    *,
    cli: str = CALLER_A,
    label: str = "UNSOLICITED_SALES",
    claimed: str | None = None,
    offset_minutes: int = 0,
    provider_call_id: str | None = None,
) -> Incident:
    n = next(_counter)
    started = BASE_TIME + timedelta(minutes=offset_minutes)
    call = CallSession(
        tenant_id=tenant_id,
        line_id=line_id,
        provider="testprov",
        provider_call_id=provider_call_id or f"CA{n:06d}-{uuid.uuid4().hex[:6]}",
        direction="INBOUND",
        started_at=started,
        ended_at=started + timedelta(seconds=40),
        duration_seconds=40,
        disposition="SCREENED",
    )
    session.add(call)
    session.flush()
    if claimed:
        session.add(
            OrganizationClaim(tenant_id=tenant_id, session_id=call.id, claimed_name=claimed)
        )
    incident = Incident(
        tenant_id=tenant_id,
        call_session_id=call.id,
        line_id=line_id,
        displayed_caller_id=cli,
        label=label,
        occurred_at=started,
    )
    session.add(incident)
    session.flush()
    return incident


def seed_consent(
    session: Session,
    tenant_id: uuid.UUID,
    line_id: uuid.UUID,
    mode: ProcessingMode,
    authorized: bool = True,
    revoked: bool = False,
) -> ConsentRecord:
    record = ConsentRecord(
        tenant_id=tenant_id,
        processing_mode=mode.value,
        legal_basis_config={"line_id": str(line_id)},
        authorized=authorized,
        authorized_at=BASE_TIME,
        revoked_at=BASE_TIME if revoked else None,
    )
    session.add(record)
    session.flush()
    return record


def seed_resolution(
    session: Session,
    tenant_id: uuid.UUID,
    line_id: uuid.UUID,
    case_id: uuid.UUID,
    attempts: int = 0,
    with_calls: bool = False,
) -> ResolutionCase:
    resolution = ResolutionCase(
        tenant_id=tenant_id, protected_line_id=line_id, enforce_case_id=case_id
    )
    session.add(resolution)
    session.flush()
    destination = ContactDestination(tenant_id=tenant_id, case_id=resolution.id, phone=CALLER_B)
    session.add(destination)
    session.flush()
    for i in range(attempts):
        call_id = None
        if with_calls:
            call = CallSession(
                tenant_id=tenant_id,
                line_id=line_id,
                provider="testprov",
                provider_call_id=f"OUT{i}-{uuid.uuid4().hex[:6]}",
                direction="OUTBOUND",
                started_at=BASE_TIME + timedelta(days=1, minutes=i),
            )
            session.add(call)
            session.flush()
            call_id = call.id
        session.add(
            ContactAttempt(
                tenant_id=tenant_id,
                case_id=resolution.id,
                destination_id=destination.id,
                call_session_id=call_id,
                outcome="IVR_FAILED" if i < attempts - 1 else "REQUEST_DELIVERED",
                started_at=BASE_TIME + timedelta(days=1, minutes=i),
                mode="HUMAN",
                idempotency_key=f"attempt-{resolution.id}-{i}",
            )
        )
    session.flush()
    return resolution


def seed_opt_out(
    session: Session,
    tenant_id: uuid.UUID,
    resolution_id: uuid.UUID,
    status: str,
    when: datetime | None = None,
) -> OptOutEvent:
    event = OptOutEvent(
        tenant_id=tenant_id,
        case_id=resolution_id,
        status=status,
        created_at=when or BASE_TIME + timedelta(days=1),
    )
    session.add(event)
    session.flush()
    return event
