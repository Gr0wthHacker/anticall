"""Filing flow: drafts, approval, manual handoff, external receipts, dedupe, supplements."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.core.config import Settings
from callshield.core.hashing import sha256_json
from callshield.db.models.enforce import (
    Case,
    CaseAuditEvent,
    FilingDraft,
    FilingSubmission,
    SubmissionApproval,
)
from callshield.db.models.filing_ext import FilingApprovalDetail, FilingDraftMeta, FilingReceipt
from callshield.domain.enums import EnforceCaseState
from callshield.filing.adapters import reset_adapters
from callshield.filing.destinations import DESTINATION_SEEDS, ensure_registry_seeded
from tests.conftest import TenantUser, TenantUserFactory
from tests.filing_helpers import (
    CaseBundle,
    add_artifact,
    add_incident,
    approval_body,
    approve,
    draft_and_approve,
    draft_body,
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
    user = make_tenant_user("Filer")
    return user, seed_case(session, user.tenant_id, user.user_id)


def _case_state(session: Session, case_id: uuid.UUID) -> str:
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return case.state


# ------------------------------------------------------------------ registry


def test_destinations_are_honest_manual_handoff(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, _ = world
    resp = client.get("/v1/filing-destinations", headers=user.headers)
    assert resp.status_code == 200
    rows = {r["slug"]: r for r in resp.json()}
    assert set(rows) == {s.slug for s in DESTINATION_SEEDS}
    for row in rows.values():
        assert row["method"] == "manual_handoff"
        assert row["electronic_submission_supported"] is False
        assert row["last_reviewed_at"].startswith("2026-10-27")
        assert row["source_url"].startswith("https://")
        assert row["retention"] and row["sunset_procedure"] and row["capability_evidence"]
    assert rows["state-ag-generic"]["capability_status"] == "NOT_VALIDATED"
    assert rows["state-ag-generic"]["validated"] is False
    fcc = " ".join(rows["fcc-unwanted-call"]["notices"])
    assert "does not resolve individual unwanted-call complaints" in fcc
    assert "not adjudication" in fcc


def test_registry_seeding_is_idempotent(session: Session) -> None:
    from callshield.db.models.enforce import DestinationRegistry

    ensure_registry_seeded(session)
    session.commit()
    ensure_registry_seeded(session)
    session.commit()
    count = len(session.execute(select(DestinationRegistry)).scalars().all())
    assert count == len(DESTINATION_SEEDS)


# ------------------------------------------------------------------ drafts


def test_draft_is_exact_payload_with_digest_and_sources(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    assert draft["payload_digest"] == sha256_json(draft["payload"])
    assert draft["revision"] == 1 and draft["kind"] == "ORIGINAL"
    assert draft["completeness"]["complete"] is True
    kinds = {s["kind"] for s in draft["sources"]}
    assert {"incident", "ledger_event", "user_input"} <= kinds
    payload = draft["payload"]
    assert len(payload["calls"]) == 2
    assert payload["claimed_caller_names"] == [
        {"name": "Acme Savings LLC", "label": "CLAIMED", "verified": False}
    ]
    assert "unverified" in payload["complaint_text"].lower()
    assert draft["prepared_submission"]["official_url"].startswith("https://www.donotcall.gov")
    # a generated draft never moves the case toward SUBMITTED
    assert _case_state(session, bundle.case_id) == EnforceCaseState.DRAFT_READY.value
    again = client.get(f"/v1/filing-drafts/{draft['draft_id']}", headers=user.headers).json()
    assert again["payload_digest"] == draft["payload_digest"]
    assert _case_state(session, bundle.case_id) == EnforceCaseState.DRAFT_READY.value


def test_incomplete_draft_lists_missing_prerequisites(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id, fields={})
    missing = {m["field"] for m in draft["completeness"]["missing"]}
    assert missing == {"contact_kind", "dnc_registration_status"}
    assert draft["completeness"]["complete"] is False
    assert "legal validity" in draft["completeness"]["note"]
    assert draft["review_state"] == "DRAFT"
    assert _case_state(session, bundle.case_id) == EnforceCaseState.ELIGIBLE_TO_DRAFT.value
    resp = approve(client, user.headers, draft)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INCOMPLETE_DRAFT"


def test_unverified_line_blocks_completeness(
    client: TestClient, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Unverified")
    bundle = seed_case(session, user.tenant_id, user.user_id, line_status="UNVERIFIED")
    draft = make_draft(client, user.headers, bundle.case_id)
    assert "complainant_line" in {m["field"] for m in draft["completeness"]["missing"]}


def test_bad_fields_rejected(client: TestClient, world: tuple[TenantUser, CaseBundle]) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/filings/drafts"
    unknown = client.post(url, json=draft_body(fields={"ssn": "x"}), headers=user.headers)
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "UNKNOWN_FIELDS"
    bad_enum = client.post(
        url, json=draft_body(fields={"contact_kind": "CARRIER_PIGEON"}), headers=user.headers
    )
    assert bad_enum.json()["error"]["code"] == "INVALID_FIELD"
    forbidden = client.post(
        url,
        json=draft_body(fields={"narrative": "VIOLATION_PROVEN, owes damages"}),
        headers=user.headers,
    )
    assert forbidden.json()["error"]["code"] == "FORBIDDEN_CONTENT"
    no_dest = client.post(url, json=draft_body("no-such-destination"), headers=user.headers)
    assert no_dest.status_code == 404


def test_state_ag_destination_is_not_validated(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    resp = client.post(
        f"/v1/cases/{bundle.case_id}/filings/drafts",
        json=draft_body("state-ag-generic"),
        headers=user.headers,
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "DESTINATION_NOT_VALIDATED"
    assert err["details"]["manual_handoff"]["validated"] is False


def test_case_in_early_state_is_not_eligible(
    client: TestClient, session: Session, make_tenant_user: TenantUserFactory
) -> None:
    user = make_tenant_user("Early")
    bundle = seed_case(
        session, user.tenant_id, user.user_id, state=EnforceCaseState.EVIDENCE_COLLECTING
    )
    resp = client.post(
        f"/v1/cases/{bundle.case_id}/filings/drafts", json=draft_body(), headers=user.headers
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CASE_NOT_ELIGIBLE"


def test_revisions_are_immutable_and_supersede(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    first = make_draft(client, user.headers, bundle.case_id)
    fields = dict(first["payload"]["fields"], narrative="A different but long enough description.")
    second = make_draft(client, user.headers, bundle.case_id, fields=fields)
    assert second["revision"] == 2
    assert second["supersedes_draft_id"] == first["draft_id"]
    assert second["payload_digest"] != first["payload_digest"]
    old = client.get(f"/v1/filing-drafts/{first['draft_id']}", headers=user.headers).json()
    assert old["review_state"] == "SUPERSEDED"
    assert old["payload"] == first["payload"] and old["payload_digest"] == first["payload_digest"]
    assert approve(client, user.headers, old).status_code == 409


def test_identical_request_returns_same_draft(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    a = make_draft(client, user.headers, bundle.case_id, idempotency_key="k-1")
    b = make_draft(client, user.headers, bundle.case_id, idempotency_key="k-1")
    c = make_draft(client, user.headers, bundle.case_id)
    assert a["draft_id"] == b["draft_id"] == c["draft_id"]
    assert b["created"] is False and c["created"] is False


def test_exclusion_rules_for_attachments(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    tid = user.tenant_id
    audio = add_artifact(
        session,
        tid,
        bundle.case_id,
        source_type="AUDIO_RECORDING",
        media_type="audio/wav",
        sharing="PERMITTED",
        processing="PERMITTED",
    )
    third = add_artifact(
        session, tid, bundle.case_id, provenance={"contains_third_party_speech": True}
    )
    private = add_artifact(session, tid, bundle.case_id, sharing="NOT_EVALUATED")
    shot = add_artifact(session, tid, bundle.case_id)
    scope = {
        "artifact_ids": [str(a.id) for a in (audio, third, private, shot)],
        "include_audio": True,
    }
    draft = make_draft(client, user.headers, bundle.case_id, scope=scope)
    reasons = {e["id"]: e["reason"] for e in draft["exclusions"] if e["id"]}
    assert reasons[str(audio.id)] == "UNCONSENTED_AUDIO"
    assert reasons[str(third.id)] == "THIRD_PARTY_CONTENT_WITHOUT_CONSENT"
    assert reasons[str(private.id)] == "SHARING_NOT_AUTHORIZED"
    assert reasons[str(shot.id)] == "NOT_SUPPORTED_BY_DESTINATION"
    assert draft["payload"]["attachments"] == []


def test_unrelated_names_never_included(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    other = add_incident(session, user.tenant_id, bundle, 5, claimed_name="Unrelated Bystander Inc")
    session.commit()
    scope = {"incident_ids": [str(bundle.incident_ids[0])]}
    draft = make_draft(client, user.headers, bundle.case_id, scope=scope)
    text = str(draft["payload"])
    assert "Unrelated Bystander" not in text and str(other.id) not in text
    assert user.email not in text
    assert len(draft["payload"]["calls"]) == 1
    no_names = make_draft(
        client,
        user.headers,
        bundle.case_id,
        scope={"include_claimed_names": False},
        fields={**draft["payload"]["fields"], "narrative": "x" * 30},
    )
    assert no_names["payload"]["claimed_caller_names"] == []


# ------------------------------------------------------------------ approval


def test_approval_requires_every_attestation_element(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    url = f"/v1/filing-drafts/{draft['draft_id']}/approve"
    base = approval_body(draft)
    cases: list[tuple[dict[str, Any], int, str]] = [
        (
            {"accepted_statement_ids": base["accepted_statement_ids"][:-1]},
            422,
            "ATTESTATION_INCOMPLETE",
        ),
        ({"displayed_items": ["complaint_text"]}, 422, "ATTESTATION_INCOMPLETE"),
        ({"sharing_confirmed": False}, 422, "ATTESTATION_INCOMPLETE"),
        ({"attestation_version": "filing-attestation-v0"}, 422, "ATTESTATION_INCOMPLETE"),
        ({"payload_digest": "0" * 64}, 409, "DIGEST_MISMATCH"),
        ({"evidence_snapshot_digest": "1" * 64}, 409, "SNAPSHOT_CHANGED"),
    ]
    for override, status, code in cases:
        resp = client.post(url, json={**base, **override}, headers=user.headers)
        assert resp.status_code == status, (override, resp.text)
        assert resp.json()["error"]["code"] == code
    assert client.post(url, json={**base, "extra": 1}, headers=user.headers).status_code == 422


def test_approval_records_all_guard_inputs(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    resp = approve(client, user.headers, draft, idempotency_key="appr-1")
    assert resp.status_code == 200
    out = resp.json()
    assert out["approved_payload_digest"] == draft["payload_digest"]
    assert out["attestation_version"] == "filing-attestation-v1"
    assert out["user_id"] == str(user.user_id)
    assert out["evidence_snapshot_digest"] == draft["evidence_snapshot_digest"]
    session.expire_all()
    detail = session.execute(select(FilingApprovalDetail)).scalar_one()
    assert detail.line_authorized and detail.destination_validated and detail.sharing_confirmed
    assert len(detail.statement_ids) == 5 and "complaint_text" in detail.displayed_items
    assert "contact_kind" in detail.completed_fields
    assert _case_state(session, bundle.case_id) == EnforceCaseState.USER_APPROVED.value
    replay = approve(client, user.headers, draft, idempotency_key="appr-1").json()
    assert replay["approval_id"] == out["approval_id"] and replay["created"] is False
    assert len(session.execute(select(SubmissionApproval)).scalars().all()) == 1
    audit = session.execute(
        select(CaseAuditEvent).where(CaseAuditEvent.action == "filing.approved")
    ).scalar_one()
    assert audit.event_data["scope"] == "filing.approve"
    assert audit.event_data["authorization_basis"]
    assert audit.event_data["idempotency_key"]
    assert audit.actor_id == f"user:{user.user_id}"


def test_new_evidence_after_draft_blocks_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    add_incident(session, user.tenant_id, bundle, 9)
    session.commit()
    resp = approve(client, user.headers, draft)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "SNAPSHOT_CHANGED"
    assert _case_state(session, bundle.case_id) == EnforceCaseState.DRAFT_READY.value


def test_unauthorized_line_blocks_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    from callshield.db.models.defend import ProtectedLine

    line = session.get(ProtectedLine, bundle.line_id)
    assert line is not None
    line.verification_status = "REVOKED"
    session.commit()
    resp = approve(client, user.headers, draft)
    assert resp.status_code in (409, 422)
    assert _case_state(session, bundle.case_id) != EnforceCaseState.USER_APPROVED.value


# ------------------------------------------------------------------ manual handoff (#7)


def test_unsupported_destination_blocks_and_gives_manual_path(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    resp = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=user.headers
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "UNSUPPORTED_DESTINATION"
    details = err["details"]
    assert details["manual_handoff"]["official_url"].startswith("https://www.donotcall.gov")
    assert details["prepared_submission"]["copyable_text"] == draft["payload"]["complaint_text"]
    assert details["prepared_submission"]["payload_digest"] == draft["payload_digest"]
    assert (
        _case_state(session, bundle.case_id) == EnforceCaseState.EXTERNAL_SUBMISSION_REQUIRED.value
    )
    filing = client.get(f"/v1/filings/{details['filing_id']}", headers=user.headers).json()
    assert filing["state"] == "EXTERNAL_SUBMISSION_REQUIRED"
    assert filing["receipts"] == []
    assert "agency" not in filing["next_step"].lower() or "official" in filing["next_step"].lower()
    again = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=user.headers
    )
    assert again.json()["error"]["details"]["filing_id"] == details["filing_id"]
    assert len(session.execute(select(FilingSubmission)).scalars().all()) == 1


def test_unsupported_submit_without_approval_does_not_change_case(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    resp = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=user.headers
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "UNSUPPORTED_DESTINATION"
    assert resp.json()["error"]["details"]["approval_required_before_filing"] is True
    assert "filing_id" not in resp.json()["error"]["details"]
    assert _case_state(session, bundle.case_id) == EnforceCaseState.DRAFT_READY.value
    assert session.execute(select(FilingSubmission)).scalars().all() == []


def test_external_receipt_with_user_supplied_number(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    filed = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    body = {
        "filed_at": filed,
        "endpoint": "https://www.donotcall.gov/report.html",
        "confirmation_number": "DNC-12345",
        "response_text": "Thanks. Call me at +14155550199 for details",
        "idempotency_key": "ext-1",
    }
    resp = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/external-receipt", json=body, headers=user.headers
    )
    assert resp.status_code == 200, resp.text
    filing = resp.json()
    assert filing["state"] == "SUBMITTED_EXTERNALLY"
    receipt = filing["receipts"][0]
    assert (
        receipt["receipt_kind"] == "USER_ATTESTED" and receipt["evidence_label"] == "USER_ATTESTED"
    )
    assert receipt["confirmation_number"] == "DNC-12345"
    assert receipt["confirmation_source"] == "USER_SUPPLIED"
    assert receipt["is_agency_acknowledgment"] is False
    assert receipt["payload_digest"] == draft["payload_digest"]
    assert "+14155550199" not in str(receipt["sanitized_response"])
    assert _case_state(session, bundle.case_id) == EnforceCaseState.SUBMITTED.value
    replay = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/external-receipt", json=body, headers=user.headers
    )
    assert replay.json()["filing_id"] == filing["filing_id"] and replay.json()["created"] is False
    assert len(session.execute(select(FilingReceipt)).scalars().all()) == 1


def test_external_receipt_without_number_invents_nothing(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    resp = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/external-receipt",
        json={"filed_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
        headers=user.headers,
    )
    receipt = resp.json()["receipts"][0]
    assert receipt["confirmation_number"] is None and receipt["confirmation_source"] == "NONE"
    session.expire_all()
    sub = session.execute(select(FilingSubmission)).scalar_one()
    assert sub.agency_receipt is None and sub.transport_response_ref is None
    assert sub.state == "SUBMITTED_EXTERNALLY"


def test_external_receipt_validation(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = make_draft(client, user.headers, bundle.case_id)
    url = f"/v1/filing-drafts/{draft['draft_id']}/external-receipt"
    now = datetime.now(UTC)
    unapproved = client.post(url, json={"filed_at": now.isoformat()}, headers=user.headers)
    assert unapproved.status_code == 409 and unapproved.json()["error"]["code"] == "NOT_APPROVED"
    assert approve(client, user.headers, draft).status_code == 200
    future = client.post(
        url, json={"filed_at": (now + timedelta(days=1)).isoformat()}, headers=user.headers
    )
    assert future.status_code == 422
    early = client.post(
        url, json={"filed_at": (now - timedelta(days=1)).isoformat()}, headers=user.headers
    )
    assert early.json()["error"]["code"] == "FILED_BEFORE_APPROVAL"
    naive = client.post(url, json={"filed_at": "2026-10-27T10:00:00"}, headers=user.headers)
    assert naive.status_code == 422
    assert _case_state(session, bundle.case_id) == EnforceCaseState.USER_APPROVED.value


# ------------------------------------------------------------------ dedupe & supplements


def _file_externally(client: TestClient, user: TenantUser, draft: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/external-receipt",
        json={"filed_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
        headers=user.headers,
    )
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


def test_duplicate_complaint_is_blocked(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    filing = _file_externally(client, user, draft)
    dup = client.post(
        f"/v1/cases/{bundle.case_id}/filings/drafts", json=draft_body(), headers=user.headers
    )
    assert dup.status_code in (409, 422)
    assert dup.json()["error"]["code"] in {"DUPLICATE_FILING", "CASE_NOT_ELIGIBLE"}
    edited = dict(draft["payload"]["fields"], narrative="Changed text that is long enough to pass.")
    changed = client.post(
        f"/v1/cases/{bundle.case_id}/filings/drafts",
        json=draft_body(fields=edited),
        headers=user.headers,
    )
    assert changed.status_code == 409
    assert len(session.execute(select(FilingSubmission)).scalars().all()) == 1
    assert filing["state"] == "SUBMITTED_EXTERNALLY"


def test_duplicate_digest_across_draft_rows_is_blocked(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    """Even a hand-made second draft with identical content cannot create a 2nd active filing."""
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    first = _file_externally(client, user, draft)
    session.expire_all()
    old_draft = session.get(FilingDraft, uuid.UUID(draft["draft_id"]))
    meta = session.execute(select(FilingDraftMeta)).scalar_one()
    approval = session.execute(select(SubmissionApproval)).scalar_one()
    assert old_draft is not None
    twin = FilingDraft(
        tenant_id=user.tenant_id,
        case_id=bundle.case_id,
        agency=old_draft.agency,
        template_version=old_draft.template_version,
        revision=2,
        draft_json=old_draft.draft_json,
        evidence_snapshot_digest=old_draft.evidence_snapshot_digest,
        review_state="APPROVED",
    )
    session.add(twin)
    session.flush()
    session.add(
        FilingDraftMeta(
            tenant_id=user.tenant_id,
            draft_id=twin.id,
            case_id=bundle.case_id,
            lineage_id=meta.lineage_id,
            destination_id=meta.destination_id,
            destination_slug=meta.destination_slug,
            kind="ORIGINAL",
            payload_digest=meta.payload_digest,
            completeness=meta.completeness,
            created_by_user_id=user.user_id,
        )
    )
    twin_approval = SubmissionApproval(
        tenant_id=user.tenant_id,
        draft_id=twin.id,
        user_id=user.user_id,
        approved_payload_digest=approval.approved_payload_digest,
        attestation_version=approval.attestation_version,
        authorized_at=approval.authorized_at,
    )
    session.add(twin_approval)
    session.flush()
    detail = session.execute(select(FilingApprovalDetail)).scalar_one()
    session.add(
        FilingApprovalDetail(
            tenant_id=user.tenant_id,
            approval_id=twin_approval.id,
            draft_id=twin.id,
            statement_ids=detail.statement_ids,
            displayed_items=detail.displayed_items,
            completed_fields=detail.completed_fields,
            sharing_confirmed=True,
            destination_validated=True,
            line_authorized=True,
            evidence_snapshot_digest=detail.evidence_snapshot_digest,
        )
    )
    session.commit()
    resp = client.post(
        f"/v1/filing-drafts/{twin.id}/external-receipt",
        json={"filed_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
        headers=user.headers,
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "DUPLICATE_SUBMISSION"
    assert resp.json()["error"]["details"]["filing_id"] == first["filing_id"]


def test_new_evidence_requires_separate_supplement_and_new_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    filing = _file_externally(client, user, draft)
    url = f"/v1/filings/{filing['filing_id']}/supplements"
    body = draft_body(fields=draft["payload"]["fields"])
    nothing_new = client.post(url, json=body, headers=user.headers)
    assert (
        nothing_new.status_code == 409 and nothing_new.json()["error"]["code"] == "NO_NEW_EVIDENCE"
    )
    add_incident(session, user.tenant_id, bundle, 11)
    session.commit()
    resp = client.post(url, json=body, headers=user.headers)
    assert resp.status_code == 201, resp.text
    sup = resp.json()
    assert sup["kind"] == "SUPPLEMENT" and sup["supplements_filing_id"] == filing["filing_id"]
    assert sup["draft_id"] != draft["draft_id"] and sup["payload_digest"] != draft["payload_digest"]
    assert sup["payload"]["supplement"]["original_payload_digest"] == draft["payload_digest"]
    assert len(sup["payload"]["calls"]) == 3
    # old filing untouched
    old = client.get(f"/v1/filings/{filing['filing_id']}", headers=user.headers).json()
    assert (
        old["state"] == "SUBMITTED_EXTERNALLY" and old["payload_digest"] == draft["payload_digest"]
    )
    assert len(old["receipts"]) == 1
    olddraft = client.get(f"/v1/filing-drafts/{draft['draft_id']}", headers=user.headers).json()
    assert olddraft["payload"] == draft["payload"] and olddraft["review_state"] == "APPROVED"
    # supplement has no approval of its own: cannot be recorded as filed
    unapproved = client.post(
        f"/v1/filing-drafts/{sup['draft_id']}/external-receipt",
        json={"filed_at": datetime.now(UTC).isoformat()},
        headers=user.headers,
    )
    assert unapproved.status_code == 409 and unapproved.json()["error"]["code"] == "NOT_APPROVED"
    assert approve(client, user.headers, sup).status_code == 200
    second = _file_externally(client, user, sup)
    assert second["filing_id"] != filing["filing_id"] and second["kind"] == "SUPPLEMENT"
    assert _case_state(session, bundle.case_id) == EnforceCaseState.SUBMITTED.value
    still = client.get(f"/v1/filings/{filing['filing_id']}", headers=user.headers).json()
    assert still["payload_digest"] == draft["payload_digest"] and len(still["receipts"]) == 1


def test_supplement_requires_filed_submission_and_same_destination(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    submit = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/submit", json={}, headers=user.headers
    )
    pending_id = submit.json()["error"]["details"]["filing_id"]
    url = f"/v1/filings/{pending_id}/supplements"
    resp = client.post(url, json=draft_body(), headers=user.headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NOT_FILED"
    _file_externally(client, user, draft)
    add_incident(session, user.tenant_id, bundle, 12)
    session.commit()
    wrong = client.post(url, json=draft_body("fcc-unwanted-call"), headers=user.headers)
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "DESTINATION_MISMATCH"


def test_edit_after_approval_voids_old_approval(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    draft = draft_and_approve(client, user.headers, bundle.case_id)
    fields = dict(draft["payload"]["fields"], narrative="Edited narrative with enough characters.")
    new = make_draft(client, user.headers, bundle.case_id, fields=fields)
    assert _case_state(session, bundle.case_id) == EnforceCaseState.DRAFT_READY.value
    old_submit = client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/external-receipt",
        json={"filed_at": datetime.now(UTC).isoformat()},
        headers=user.headers,
    )
    assert old_submit.status_code == 409
    assert old_submit.json()["error"]["code"] == "APPROVAL_INVALID"
    assert (
        client.post(
            f"/v1/filing-drafts/{new['draft_id']}/external-receipt",
            json={"filed_at": datetime.now(UTC).isoformat()},
            headers=user.headers,
        ).json()["error"]["code"]
        == "NOT_APPROVED"
    )


# ------------------------------------------------------------------ cross-tenant


def test_cross_tenant_denial_everywhere(
    client: TestClient, session: Session, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    bundle = seed_case(session, a.tenant_id, a.user_id)
    draft = draft_and_approve(client, a.headers, bundle.case_id)
    filing = _file_externally(client, a, draft)
    did, fid = draft["draft_id"], filing["filing_id"]
    ts = datetime.now(UTC).isoformat()
    probes = [
        client.post(
            f"/v1/cases/{bundle.case_id}/filings/drafts", json=draft_body(), headers=b.headers
        ),
        client.get(f"/v1/filing-drafts/{did}", headers=b.headers),
        client.post(
            f"/v1/filing-drafts/{did}/approve", json=approval_body(draft), headers=b.headers
        ),
        client.post(f"/v1/filing-drafts/{did}/submit", json={}, headers=b.headers),
        client.post(
            f"/v1/filing-drafts/{did}/external-receipt", json={"filed_at": ts}, headers=b.headers
        ),
        client.get(f"/v1/filings/{fid}", headers=b.headers),
        client.post(f"/v1/filings/{fid}/supplements", json=draft_body(), headers=b.headers),
        client.post(f"/v1/filings/{fid}/status-check", headers=b.headers),
    ]
    for resp in probes:
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        assert draft["payload_digest"] not in resp.text
    assert client.get(f"/v1/filing-drafts/{did}", headers=a.headers).status_code == 200
    assert client.get("/v1/filing-destinations").status_code == 401


def test_no_forbidden_strings_in_rendered_drafts(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    import re

    user, bundle = world
    pattern = re.compile(r"VIOLATION_PROVEN|damages?|\$\d|liable|guilty", re.IGNORECASE)
    for slug in ("ftc-donotcall", "ftc-reportfraud", "fcc-unwanted-call", "carrier-generic"):
        fields = {
            "contact_kind": "LIVE_CALL",
            "dnc_registration_status": "REGISTERED",
            "payment_made": "NO",
            "carrier_name": "ExampleCell",
            "narrative": "Repeated unwanted sales calls to my number over two weeks.",
        }
        allowed = {
            "ftc-donotcall": {"contact_kind", "dnc_registration_status", "narrative"},
            "ftc-reportfraud": {"payment_made", "narrative"},
            "fcc-unwanted-call": {"contact_kind", "narrative"},
            "carrier-generic": {"carrier_name", "narrative"},
        }[slug]
        fields = {k: v for k, v in fields.items() if k in allowed}
        draft = make_draft(client, user.headers, bundle.case_id, destination=slug, fields=fields)
        rendered = client.get(f"/v1/filing-drafts/{draft['draft_id']}", headers=user.headers).text
        assert pattern.search(rendered) is None, slug
        assert draft["completeness"]["complete"] is True, (slug, draft["completeness"])
