"""Shared test data builders and the TEST-ONLY fake documented_api adapter.

The fake never touches a network. It exists to prove the submission pipeline's
invariants (exact payload, no false SUBMITTED, UNKNOWN on ambiguity, no blind retry).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from callshield.core.ids import new_id
from callshield.db.models.defend import CallSession, Incident, OrganizationClaim, ProtectedLine
from callshield.db.models.enforce import Case, CaseIncident, EvidenceArtifact
from callshield.domain.enums import (
    AdapterMethod,
    EnforceCaseState,
    EventOrigin,
    LinkageType,
    SubmissionState,
)
from callshield.domain.topics import Topic
from callshield.filing.adapters import (
    ApprovedSubmission,
    FilingDraftView,
    FilingStatus,
    FilingStatusState,
    PreparedSubmission,
    SubmissionReceipt,
    TransportOutcome,
    ValidationReport,
    register_documented_adapter,
)
from callshield.filing.attestation import ATTESTATION_VERSION, required_statement_ids
from callshield.filing.destinations import DestinationSeed, upsert_destination
from callshield.filing.submit import TransportRejectedError
from callshield.ledger.service import append_event

LINE_E164 = "+14155550123"
CALLER_E164 = "+14155550177"
FAKE_SLUG = "test-fake-api"
T0 = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


@dataclass
class CaseBundle:
    case_id: uuid.UUID
    line_id: uuid.UUID
    incident_ids: list[uuid.UUID] = field(default_factory=list)
    session_ids: list[uuid.UUID] = field(default_factory=list)


def seed_case(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    incidents: int = 2,
    line_status: str = "VERIFIED",
    state: EnforceCaseState = EnforceCaseState.ELIGIBLE_TO_DRAFT,
    claimed_name: str | None = "Acme Savings LLC",
) -> CaseBundle:
    line = ProtectedLine(
        tenant_id=tenant_id, owner_user_id=user_id, e164=LINE_E164, verification_status=line_status
    )
    session.add(line)
    session.flush()
    case = Case(
        tenant_id=tenant_id, protected_line_id=line.id, title="Test case", state=state.value
    )
    session.add(case)
    session.flush()
    bundle = CaseBundle(case.id, line.id)
    for n in range(incidents):
        add_incident(session, tenant_id, bundle, n, claimed_name if n == 0 else None)
    session.commit()
    return bundle


def add_incident(
    session: Session,
    tenant_id: uuid.UUID,
    bundle: CaseBundle,
    n: int,
    claimed_name: str | None = None,
    linkage: LinkageType = LinkageType.USER_LINK,
    when: datetime | None = None,
) -> Incident:
    call = CallSession(
        tenant_id=tenant_id,
        line_id=bundle.line_id,
        provider="fake",
        provider_call_id=f"call-{new_id()}",
        direction="INBOUND",
        started_at=when or T0 + timedelta(days=n),
    )
    session.add(call)
    session.flush()
    inc = Incident(
        tenant_id=tenant_id,
        call_session_id=call.id,
        line_id=bundle.line_id,
        displayed_caller_id=CALLER_E164,
        label="UNSOLICITED_SALES",
        status="LINKED",
        evidence_label="OBSERVED",
        occurred_at=when or T0 + timedelta(days=n),
    )
    session.add(inc)
    session.flush()
    session.add(
        CaseIncident(
            tenant_id=tenant_id,
            case_id=bundle.case_id,
            incident_id=inc.id,
            linkage_type=linkage.value,
        )
    )
    if claimed_name:
        session.add(
            OrganizationClaim(tenant_id=tenant_id, session_id=call.id, claimed_name=claimed_name)
        )
    append_event(
        session,
        tenant_id,
        Topic.CALL_RECEIVED,
        {"incident_id": str(inc.id), "call_session_id": str(call.id)},
        EventOrigin.PROVIDER_WEBHOOK,
        f"evt-{call.provider_call_id}",
    )
    bundle.incident_ids.append(inc.id)
    bundle.session_ids.append(call.id)
    session.flush()
    return inc


def add_artifact(
    session: Session,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    source_type: str = "SCREENSHOT",
    media_type: str = "image/png",
    sharing: str = "PERMITTED",
    processing: str = "PERMITTED",
    consent_id: uuid.UUID | None = None,
    provenance: dict[str, Any] | None = None,
) -> EvidenceArtifact:
    art = EvidenceArtifact(
        tenant_id=tenant_id,
        case_id=case_id,
        source_type=source_type,
        media_type=media_type,
        sha256="ab" * 32,
        evidence_label="USER_ATTESTED",
        origin="ORIGINAL",
        processing_authorization=processing,
        sharing_authorization=sharing,
        consent_id=consent_id,
        provenance=provenance or {},
    )
    session.add(art)
    session.commit()
    return art


DEFAULT_FIELDS = {
    "contact_kind": "LIVE_CALL",
    "dnc_registration_status": "REGISTERED",
    "narrative": "Repeated sales calls about a loan offer after I asked them to stop.",
}


def draft_body(slug: str = "ftc-donotcall", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"destination": slug, "fields": dict(DEFAULT_FIELDS)}
    body.update(overrides)
    return body


def make_draft(
    client: TestClient, headers: dict[str, str], case_id: uuid.UUID, **kwargs: Any
) -> dict[str, Any]:
    resp = client.post(
        f"/v1/cases/{case_id}/filings/drafts", json=draft_body(**kwargs), headers=headers
    )
    assert resp.status_code in (200, 201), resp.text
    data: dict[str, Any] = resp.json()
    return data


def approval_body(draft: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "payload_digest": draft["payload_digest"],
        "evidence_snapshot_digest": draft["evidence_snapshot_digest"],
        "attestation_version": ATTESTATION_VERSION,
        "accepted_statement_ids": required_statement_ids(),
        "displayed_items": [
            "complaint_text",
            "recipient",
            "submission_channel",
            "attachments",
        ],
        "sharing_confirmed": True,
    }
    body.update(overrides)
    return body


def approve(
    client: TestClient, headers: dict[str, str], draft: dict[str, Any], **overrides: Any
) -> Any:
    return client.post(
        f"/v1/filing-drafts/{draft['draft_id']}/approve",
        json=approval_body(draft, **overrides),
        headers=headers,
    )


def draft_and_approve(
    client: TestClient, headers: dict[str, str], case_id: uuid.UUID, **kwargs: Any
) -> dict[str, Any]:
    draft = make_draft(client, headers, case_id, **kwargs)
    resp = approve(client, headers, draft)
    assert resp.status_code == 200, resp.text
    return draft


class FakeDocumentedApi:
    """TEST ONLY. Behavior is scripted per `mode`; records everything it was asked to send."""

    id = "fake_documented_api"
    destination_name = "Fake documented API (tests)"
    method = AdapterMethod.DOCUMENTED_API
    supported_issue_types: tuple[str, ...] = ("DNC_REGISTRY",)

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.sent: list[ApprovedSubmission] = []
        self.status = FilingStatus(FilingStatusState.NOT_FOUND)
        self.status_calls = 0

    def validate(self, draft: FilingDraftView) -> ValidationReport:
        return ValidationReport(True)

    def prepare(self, draft: FilingDraftView) -> PreparedSubmission:
        raise NotImplementedError

    def submit(self, submission: ApprovedSubmission) -> SubmissionReceipt:
        self.sent.append(submission)
        mode = self.mode
        if mode == "timeout":
            raise TimeoutError("no answer")
        if mode == "reject":
            raise TransportRejectedError("HTTP_422")
        if mode == "garbage":
            return "<html>maybe?</html>"  # type: ignore[return-value]
        if mode == "no_ref":
            return SubmissionReceipt(TransportOutcome.ACCEPTED, transport_ref=None)
        if mode == "blank_ref":
            return SubmissionReceipt(TransportOutcome.ACCEPTED, transport_ref="   ")
        if mode == "ambiguous":
            return SubmissionReceipt(TransportOutcome.AMBIGUOUS, transport_ref="tx-maybe")
        if mode == "agency":
            return SubmissionReceipt(
                TransportOutcome.ACCEPTED, transport_ref="tx-1", agency_confirmation="AGENCY-77"
            )
        if mode == "agency_same":
            return SubmissionReceipt(
                TransportOutcome.ACCEPTED, transport_ref="tx-1", agency_confirmation="tx-1"
            )
        return SubmissionReceipt(
            TransportOutcome.ACCEPTED,
            transport_ref="tx-1",
            endpoint="https://fake.invalid/api",
            response={"status": "queued", "token": "should-be-redacted"},
        )

    def lookup_status(self, idempotency_key: str, payload_digest: str) -> FilingStatus:
        self.status_calls += 1
        return self.status


def install_fake_api(session: Session, mode: str = "ok") -> FakeDocumentedApi:
    """Register a documented_api destination backed by the fake adapter (tests only)."""
    seed = DestinationSeed(
        slug=FAKE_SLUG,
        agency="FTC",
        name="Fake documented API destination (tests)",
        source_url="https://fake.invalid/docs",
        template_key="ftc_dnc_v1",
        validated=True,
        supported_issue_types=("DNC_REGISTRY",),
        supported_fields=("narrative",),
        authorization_requirements="test",
        manual_handoff={"official_url": "https://fake.invalid/", "steps": ["n/a"]},
        method=AdapterMethod.DOCUMENTED_API.value,
        capability_evidence="TEST FIXTURE ONLY",
    )
    upsert_destination(session, seed)
    session.commit()
    adapter = FakeDocumentedApi(mode)
    register_documented_adapter(FAKE_SLUG, adapter, "scoped-cred-ref-test")
    return adapter


ACTIVE_OK_STATES = {
    SubmissionState.SUBMITTED.value,
    SubmissionState.AGENCY_ACKNOWLEDGED.value,
}
