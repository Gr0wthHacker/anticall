"""Documented-API submission pipeline with the TEST-ONLY fake adapter.

Covers PRD 19.13 #8-#9 and the 19.14 gates: 0 false SUBMITTED, 0 unapproved transmissions,
exact approved revision transmitted, UNKNOWN on ambiguity with no blind retry.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.core.config import Settings
from callshield.core.hashing import sha256_json
from callshield.db.models.enforce import Case, CaseAuditEvent, FilingDraft, FilingSubmission
from callshield.db.models.filing_ext import FilingReceipt
from callshield.domain.enums import EnforceCaseState as E
from callshield.filing.adapters import FilingStatus, FilingStatusState, reset_adapters
from tests.conftest import TenantUser, TenantUserFactory
from tests.filing_helpers import (
    FAKE_SLUG,
    CaseBundle,
    FakeDocumentedApi,
    add_incident,
    approve,
    draft_and_approve,
    install_fake_api,
    make_draft,
    seed_case,
)


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


@pytest.fixture
def world(make_tenant_user: TenantUserFactory, session: Session) -> tuple[TenantUser, CaseBundle]:
    user = make_tenant_user("Submitter")
    return user, seed_case(session, user.tenant_id, user.user_id)


def _case_state(session: Session, case_id: uuid.UUID) -> str:
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return case.state


def _submit(client: TestClient, user: TenantUser, draft: dict[str, Any]) -> Any:
    return client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=user.headers
    )


def _approved_fake_draft(
    client: TestClient, session: Session, user: TenantUser, bundle: CaseBundle, mode: str = "ok"
) -> tuple[FakeDocumentedApi, dict[str, Any]]:
    fake = install_fake_api(session, mode)
    draft = draft_and_approve(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    return fake, draft


def test_default_flag_is_off() -> None:
    assert Settings().filing_submission_enabled is False


def test_authorized_submission_transmits_exact_approved_revision(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)
    resp = _submit(client, user, draft)
    assert resp.status_code == 200, resp.text
    filing = resp.json()
    assert filing["state"] == "SUBMITTED"
    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent.payload == draft["payload"]
    assert sent.payload_digest == draft["payload_digest"] == sha256_json(sent.payload)
    assert sent.scoped_credential_ref == "scoped-cred-ref-test"
    receipt = filing["receipts"][0]
    assert receipt["receipt_kind"] == "TRANSPORT_ACK" and receipt["evidence_label"] == "OBSERVED"
    assert receipt["is_agency_acknowledgment"] is False and receipt["confirmation_number"] is None
    assert "should-be-redacted" not in str(receipt["sanitized_response"])
    assert receipt["payload_digest"] == draft["payload_digest"]
    assert filing["transport_response_ref"] == "tx-1"
    assert _case_state(session, bundle.case_id) == E.SUBMITTED.value
    got = client.get(f"/v1/filings/{filing['filing_id']}", headers=user.headers).json()
    assert got["state"] == "SUBMITTED" and len(got["receipts"]) == 1


def test_replay_never_transmits_twice(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)
    first = _submit(client, user, draft).json()
    second = _submit(client, user, draft)
    assert second.status_code == 200
    assert second.json()["filing_id"] == first["filing_id"] and second.json()["created"] is False
    assert len(fake.sent) == 1
    assert len(session.execute(select(FilingSubmission)).scalars().all()) == 1


def test_agency_acknowledged_only_with_distinguishable_confirmation(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _fake, draft = _approved_fake_draft(client, session, user, bundle, "agency")
    filing = _submit(client, user, draft).json()
    assert filing["state"] == "AGENCY_ACKNOWLEDGED"
    kinds = [r["receipt_kind"] for r in filing["receipts"]]
    assert kinds == ["TRANSPORT_ACK", "AGENCY_CONFIRMATION"]
    agency = filing["receipts"][1]
    assert agency["confirmation_number"] == "AGENCY-77" and agency["is_agency_acknowledgment"]
    assert _case_state(session, bundle.case_id) == E.AGENCY_ACKNOWLEDGED.value


def test_indistinguishable_confirmation_stays_submitted(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _fake, draft = _approved_fake_draft(client, session, user, bundle, "agency_same")
    filing = _submit(client, user, draft).json()
    assert filing["state"] == "SUBMITTED"
    assert [r["receipt_kind"] for r in filing["receipts"]] == ["TRANSPORT_ACK"]
    assert _case_state(session, bundle.case_id) == E.SUBMITTED.value


@pytest.mark.parametrize("mode", ["timeout", "garbage", "no_ref", "blank_ref", "ambiguous"])
def test_ambiguous_response_is_unknown_with_no_blind_retry(
    client: TestClient,
    session: Session,
    world: tuple[TenantUser, CaseBundle],
    mode: str,
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle, mode)
    resp = _submit(client, user, draft)
    assert resp.status_code == 200
    filing = resp.json()
    assert filing["state"] == "UNKNOWN"
    assert filing["receipts"] == [] and filing["transport_response_ref"] is None
    assert "do not re-submit" in filing["next_step"].lower()
    assert _case_state(session, bundle.case_id) == E.SUBMISSION_IN_PROGRESS.value
    for _ in range(3):  # repeated clicks never resend
        again = _submit(client, user, draft)
        assert again.json()["state"] == "UNKNOWN"
    assert len(fake.sent) == 1


def test_status_lookup_resolves_unknown_without_resending(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle, "timeout")
    filing_id = _submit(client, user, draft).json()["filing_id"]
    url = f"/v1/filings/{filing_id}/status-check"
    nothing = client.post(url, headers=user.headers).json()
    assert nothing["state"] == "UNKNOWN" and nothing["receipts"] == []
    fake.status = FilingStatus(FilingStatusState.RECEIVED)  # no reference: not an ack
    assert client.post(url, headers=user.headers).json()["state"] == "UNKNOWN"
    fake.status = FilingStatus(FilingStatusState.RECEIVED, transport_ref="tx-late")
    resolved = client.post(url, headers=user.headers).json()
    assert resolved["state"] == "SUBMITTED" and resolved["transport_response_ref"] == "tx-late"
    assert _case_state(session, bundle.case_id) == E.SUBMITTED.value
    fake.status = FilingStatus(
        FilingStatusState.ACKNOWLEDGED, transport_ref="tx-late", agency_confirmation="AG-1"
    )
    final = client.post(url, headers=user.headers).json()
    assert final["state"] == "AGENCY_ACKNOWLEDGED"
    assert len(fake.sent) == 1 and fake.status_calls == 4


def test_definitive_rejection_is_failed_and_needs_new_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle, "reject")
    filing = _submit(client, user, draft).json()
    assert filing["state"] == "FAILED" and filing["failure_code"] == "HTTP_422"
    assert _case_state(session, bundle.case_id) == E.SUBMISSION_FAILED.value
    again = _submit(client, user, draft)  # same approval is consumed: no second send
    assert again.json()["state"] == "FAILED" and len(fake.sent) == 1
    fields = dict(draft["payload"]["fields"], narrative="Revised after the rejection, long enough.")
    revised = make_draft(client, user.headers, bundle.case_id, slug=FAKE_SLUG, fields=fields)
    assert revised["revision"] == 2
    assert approve(client, user.headers, revised).status_code == 200
    fake.mode = "ok"
    assert _submit(client, user, revised).json()["state"] == "SUBMITTED"
    assert len(fake.sent) == 2 and fake.sent[1].payload_digest == revised["payload_digest"]


MODES = ["ok", "agency", "agency_same", "timeout", "garbage", "no_ref", "blank_ref",
         "ambiguous", "reject"]  # fmt: skip
SUBMITTED_MODES = {"ok", "agency", "agency_same"}


@pytest.mark.parametrize("mode", MODES)
def test_failure_injection_zero_false_submitted(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle], mode: str
) -> None:
    """19.14: SUBMITTED / AGENCY_ACKNOWLEDGED appear only when the injected transport earned it."""
    user, bundle = world
    _fake, draft = _approved_fake_draft(client, session, user, bundle, mode)
    state = _submit(client, user, draft).json()["state"]
    session.expire_all()
    subs = session.execute(select(FilingSubmission)).scalars().all()
    receipts = session.execute(select(FilingReceipt)).scalars().all()
    if mode in SUBMITTED_MODES:
        assert state in {"SUBMITTED", "AGENCY_ACKNOWLEDGED"}
        assert any(r.receipt_kind == "TRANSPORT_ACK" for r in receipts)
    else:
        assert state in {"UNKNOWN", "FAILED"}
        assert all(s.state not in {"SUBMITTED", "AGENCY_ACKNOWLEDGED"} for s in subs)
        assert receipts == []
        assert _case_state(session, bundle.case_id) in {
            E.SUBMISSION_IN_PROGRESS.value,
            E.SUBMISSION_FAILED.value,
        }


# ------------------------------------------------------------- 0 unapproved transmissions


def test_no_transmission_without_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake = install_fake_api(session)
    draft = make_draft(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    resp = _submit(client, user, draft)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NOT_APPROVED"
    assert fake.sent == []
    assert _case_state(session, bundle.case_id) == E.DRAFT_READY.value


def test_no_transmission_when_payload_tampered_after_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)
    row = session.get(FilingDraft, uuid.UUID(draft["draft_id"]))
    assert row is not None
    doc = dict(row.draft_json)
    payload = dict(doc["payload"])
    payload["complaint_text"] = payload["complaint_text"] + " (edited behind the user's back)"
    doc["payload"] = payload
    row.draft_json = doc
    session.commit()
    resp = _submit(client, user, draft)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "APPROVAL_INVALID"
    assert "PAYLOAD_TAMPERED" in resp.json()["error"]["details"]["problems"]
    assert fake.sent == []
    assert session.execute(select(FilingSubmission)).scalars().all() == []


def test_no_transmission_when_evidence_changed_after_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)
    add_incident(session, user.tenant_id, bundle, 20)
    session.commit()
    resp = _submit(client, user, draft)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "APPROVAL_INVALID"
    assert "EVIDENCE_SNAPSHOT_CHANGED" in resp.json()["error"]["details"]["problems"]
    assert fake.sent == []
    assert _case_state(session, bundle.case_id) == E.DRAFT_READY.value
    refreshed = client.get(f"/v1/filing-drafts/{draft['draft_id']}", headers=user.headers).json()
    assert refreshed["review_state"] == "NEEDS_REVIEW"  # re-attestation required
    # regenerate + approve again, then it may go
    redraft = make_draft(
        client, user.headers, bundle.case_id, slug=FAKE_SLUG, fields=draft["payload"]["fields"]
    )
    assert redraft["payload_digest"] != draft["payload_digest"]
    assert approve(client, user.headers, redraft).status_code == 200
    assert _submit(client, user, redraft).json()["state"] == "SUBMITTED"
    assert len(fake.sent) == 1 and fake.sent[0].payload_digest == redraft["payload_digest"]


def test_flag_off_blocks_even_a_registered_adapter(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)
    client.app.state.settings.filing_submission_enabled = False  # type: ignore[attr-defined]
    resp = _submit(client, user, draft)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "FILING_SUBMISSION_DISABLED"
    assert fake.sent == []
    assert session.execute(select(FilingSubmission)).scalars().all() == []


def test_unregistered_documented_destination_is_unsupported(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    """A registry row claiming documented_api without a registered adapter is still blocked."""
    user, bundle = world
    install_fake_api(session)
    draft = draft_and_approve(client, user.headers, bundle.case_id, slug=FAKE_SLUG)
    reset_adapters()
    resp = _submit(client, user, draft)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "UNSUPPORTED_DESTINATION"


def test_submission_idempotency_key_is_unique_in_db(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _fake, draft = _approved_fake_draft(client, session, user, bundle)
    _submit(client, user, draft)
    session.expire_all()
    sub = session.execute(select(FilingSubmission)).scalar_one()
    clone = FilingSubmission(
        tenant_id=user.tenant_id,
        draft_id=sub.draft_id,
        approval_id=sub.approval_id,
        adapter="x",
        idempotency_key=sub.idempotency_key,
        state="PENDING",
    )
    session.add(clone)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_audit_trail_records_actor_scope_basis_key(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _fake, draft = _approved_fake_draft(client, session, user, bundle)
    _submit(client, user, draft)
    session.expire_all()
    rows = (
        session.execute(select(CaseAuditEvent).where(CaseAuditEvent.action.like("filing.%")))
        .scalars()
        .all()
    )
    actions = {r.action for r in rows}
    assert {"filing.drafted", "filing.approved", "filing.submitted"} <= actions
    for row in rows:
        assert row.actor_id == f"user:{user.user_id}" and row.actor_type == "USER"
        assert row.event_data.get("scope") and row.event_data.get("authorization_basis")
        assert "idempotency_key" in row.event_data


def test_cross_tenant_submit_and_status_denied(
    client: TestClient, session: Session, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    bundle = seed_case(session, a.tenant_id, a.user_id)
    fake, draft = _approved_fake_draft(client, session, a, bundle)
    filing = _submit(client, a, draft).json()
    assert (
        client.post(
            f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=b.headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/filings/{filing['filing_id']}/status-check", headers=b.headers
        ).status_code
        == 404
    )
    assert client.get(f"/v1/filings/{filing['filing_id']}", headers=b.headers).status_code == 404
    assert len(fake.sent) == 1


def test_local_error_after_send_becomes_unknown_never_submitted(
    client: TestClient,
    session: Session,
    world: tuple[TenantUser, CaseBundle],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from callshield.filing import submit as submit_mod

    user, bundle = world
    fake, draft = _approved_fake_draft(client, session, user, bundle)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("local bookkeeping failed after the call")

    monkeypatch.setattr(submit_mod, "_apply_receipt", boom)
    resp = _submit(client, user, draft)
    assert resp.status_code == 200 and resp.json()["state"] == "UNKNOWN"
    assert resp.json()["failure_code"] == "LOCAL_PROCESSING_ERROR_AFTER_SEND"
    assert len(fake.sent) == 1
    assert _case_state(session, bundle.case_id) == E.SUBMISSION_IN_PROGRESS.value
    assert _submit(client, user, draft).json()["state"] == "UNKNOWN" and len(fake.sent) == 1
