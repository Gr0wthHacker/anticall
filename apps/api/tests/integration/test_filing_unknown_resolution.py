"""User-driven exit from an UNKNOWN filing (never resends, never invents a receipt)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.core.clock import FrozenClock, set_clock, utcnow
from callshield.core.config import Settings
from callshield.db.models.enforce import Case, CaseAuditEvent
from callshield.db.models.filing_ext import FilingReceipt
from callshield.domain.enums import EnforceCaseState as E
from callshield.filing.adapters import reset_adapters
from tests.conftest import TenantUser, TenantUserFactory
from tests.filing_helpers import (
    FAKE_SLUG,
    CaseBundle,
    FakeDocumentedApi,
    draft_and_approve,
    install_fake_api,
    seed_case,
)

Unknown = tuple[TenantUser, CaseBundle, FakeDocumentedApi, dict[str, Any]]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        db_url="sqlite://", environment="test", allow_bootstrap=True, filing_submission_enabled=True
    )


@pytest.fixture(autouse=True)
def _clean_adapters() -> Iterator[None]:
    reset_adapters()
    yield
    reset_adapters()


def _submit(client: TestClient, user: TenantUser, draft: dict[str, Any]) -> dict[str, Any]:
    url = f"/v1/filing-drafts/{draft['draft_id']}/submit"
    body: dict[str, Any] = client.post(url, json={}, headers=user.headers).json()
    return body


@pytest.fixture
def unknown(client: TestClient, session: Session, make_tenant_user: TenantUserFactory) -> Unknown:
    """A submission that ended UNKNOWN (transport timed out) and never resolves."""
    user = make_tenant_user("Filer")
    bundle = seed_case(session, user.tenant_id, user.user_id)
    fake = install_fake_api(session, "timeout")
    draft = draft_and_approve(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    filing = _submit(client, user, draft)
    assert filing["state"] == "UNKNOWN"
    return user, bundle, fake, filing


def _resolve(client: TestClient, user: TenantUser, filing_id: str, **body: Any) -> Any:
    return client.post(f"/v1/filings/{filing_id}/resolve-unknown", json=body, headers=user.headers)


def _case_state(session: Session, case_id: uuid.UUID) -> str:
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return case.state


def test_verified_submitted_records_user_attested_receipt(
    client: TestClient, session: Session, unknown: Unknown
) -> None:
    user, bundle, fake, filing = unknown
    resp = _resolve(
        client, user, filing["filing_id"], resolution="verified_submitted",
        confirmation_number="USER-123", note="saw it on the site",
    )  # fmt: skip
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["state"] == "SUBMITTED_EXTERNALLY" and out["user_resolution"] == "verified_submitted"
    assert out["needs_user_verification"] is False
    (receipt,) = out["receipts"]
    assert receipt["evidence_label"] == "USER_ATTESTED"
    assert receipt["receipt_kind"] == "USER_ATTESTED"
    assert receipt["confirmation_number"] == "USER-123"
    assert receipt["confirmation_source"] == "USER_SUPPLIED"
    assert receipt["is_agency_acknowledgment"] is False
    assert _case_state(session, bundle.case_id) == E.SUBMITTED.value
    assert len(fake.sent) == 1  # never re-sent


def test_verified_submitted_without_number_invents_nothing(
    client: TestClient, unknown: Unknown
) -> None:
    user, _bundle, _fake, filing = unknown
    out = _resolve(client, user, filing["filing_id"], resolution="verified_submitted").json()
    (receipt,) = out["receipts"]
    assert receipt["confirmation_number"] is None and receipt["confirmation_source"] == "NONE"
    assert receipt["evidence_label"] == "USER_ATTESTED"


@pytest.mark.parametrize(
    ("resolution", "code"),
    [("verified_not_submitted", "USER_VERIFIED_NOT_SUBMITTED"), ("abandon", "USER_ABANDONED")],
)
def test_not_submitted_and_abandon_fail_the_filing_without_resending(
    client: TestClient, session: Session, unknown: Unknown, resolution: str, code: str
) -> None:
    user, bundle, fake, filing = unknown
    out = _resolve(client, user, filing["filing_id"], resolution=resolution).json()
    assert out["state"] == "FAILED" and out["failure_code"] == code and out["receipts"] == []
    assert _case_state(session, bundle.case_id) == E.SUBMISSION_FAILED.value
    again = client.post(
        f"/v1/filing-drafts/{filing['draft_id']}/submit", json={}, headers=user.headers
    )
    assert again.json()["state"] == "FAILED"  # same approval is consumed: no automatic retry
    assert len(fake.sent) == 1


def test_resolution_is_audited_and_idempotent(
    client: TestClient, session: Session, unknown: Unknown
) -> None:
    user, bundle, _fake, filing = unknown
    first = _resolve(client, user, filing["filing_id"], resolution="verified_not_submitted")
    assert first.json()["created"] is True
    replay = _resolve(client, user, filing["filing_id"], resolution="verified_not_submitted")
    assert replay.status_code == 200 and replay.json()["created"] is False
    session.expire_all()
    audits = (
        session.execute(
            select(CaseAuditEvent).where(
                CaseAuditEvent.case_id == bundle.case_id,
                CaseAuditEvent.action == "filing.unknown_resolved_by_user",
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].event_data["resolution"] == "verified_not_submitted"
    assert str(user.user_id) in str(audits[0].actor_id)
    clash = _resolve(client, user, filing["filing_id"], resolution="verified_submitted")
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "ALREADY_RESOLVED"
    assert session.execute(select(FilingReceipt)).scalars().all() == []


def test_only_unknown_filings_can_be_resolved(
    client: TestClient, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Filer")
    bundle = seed_case(session, user.tenant_id, user.user_id)
    install_fake_api(session, "ok")
    draft = draft_and_approve(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    filing = _submit(client, user, draft)
    assert filing["state"] == "SUBMITTED"
    resp = _resolve(client, user, filing["filing_id"], resolution="abandon")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NOT_UNKNOWN"


@pytest.mark.parametrize(
    "body",
    [
        {"resolution": "abandon", "confirmation_number": "X-1"},
        {"resolution": "verified_not_submitted", "confirmation_number": "X-1"},
        {"resolution": "submitted"},
        {"resolution": "abandon", "state": "SUBMITTED"},
        {},
    ],
)
def test_invalid_bodies_are_rejected(
    client: TestClient, unknown: Unknown, body: dict[str, Any]
) -> None:
    user, _bundle, _fake, filing = unknown
    assert _resolve(client, user, filing["filing_id"], **body).status_code == 422
    got = client.get(f"/v1/filings/{filing['filing_id']}", headers=user.headers).json()
    assert got["state"] == "UNKNOWN"


def test_cross_tenant_resolve_is_404_and_changes_nothing(
    client: TestClient, unknown: Unknown, make_tenant_user: TenantUserFactory
) -> None:
    user, _bundle, _fake, filing = unknown
    other = make_tenant_user("Other")
    assert _resolve(client, other, filing["filing_id"], resolution="abandon").status_code == 404
    url = f"/v1/filings/{filing['filing_id']}"
    assert client.get(url, headers=other.headers).status_code == 404
    got = client.get(url, headers=user.headers).json()
    assert got["state"] == "UNKNOWN" and got["user_resolution"] is None


def test_unknown_age_flag_after_configured_hours(
    client: TestClient, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    clock = FrozenClock(utcnow())
    set_clock(clock)
    user = make_tenant_user("Filer")
    bundle = seed_case(session, user.tenant_id, user.user_id)
    install_fake_api(session, "timeout")
    draft = draft_and_approve(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    filing = _submit(client, user, draft)
    url = f"/v1/filings/{filing['filing_id']}"
    fresh = client.get(url, headers=user.headers).json()
    assert fresh["state"] == "UNKNOWN" and fresh["needs_user_verification"] is False
    assert fresh["unknown_since"] is not None
    clock.advance(hours=23, minutes=59)
    assert client.get(url, headers=user.headers).json()["needs_user_verification"] is False
    clock.current += timedelta(minutes=2)
    assert client.get(url, headers=user.headers).json()["needs_user_verification"] is True
    resolved = _resolve(client, user, filing["filing_id"], resolution="abandon").json()
    assert resolved["needs_user_verification"] is False


def test_age_threshold_is_configurable_and_defaults_to_24h() -> None:
    assert Settings().filing_unknown_verify_after_hours == 24
    assert Settings(filing_unknown_verify_after_hours=2).filing_unknown_verify_after_hours == 2
    with pytest.raises(ValueError):
        Settings(filing_unknown_verify_after_hours=0)
