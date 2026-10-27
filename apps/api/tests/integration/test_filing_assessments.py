"""POST /v1/cases/{id}/assess: deterministic, versioned, issue-spotting only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.assessment.facts import UserFacts
from callshield.assessment.rulesets import CURRENT_RULESET_VERSION
from callshield.assessment.safety import find_forbidden
from callshield.assessment.service import run_assessment
from callshield.db.models.defend import Classification
from callshield.db.models.enforce import Case, CaseAssessment
from callshield.db.models.filing_ext import AssessmentRun
from callshield.db.models.resolve import OptOutEvent, ResolutionCase
from callshield.domain.enums import AssessmentStatus
from tests.conftest import TenantUser, TenantUserFactory
from tests.filing_helpers import CaseBundle, add_incident, seed_case

ALLOWED = {s.value for s in AssessmentStatus}
FULL_FACTS: dict[str, Any] = {
    "call_type": "TELEMARKETING",
    "exemption_status": "NONE_KNOWN",
    "entity_specific_request_on": "2026-10-02",
    "dnc_registered_on": "2026-08-01",
    "technology": "PRERECORDED",
    "called_party_state": "CA",
}


@pytest.fixture
def world(make_tenant_user: TenantUserFactory, session: Session) -> tuple[TenantUser, CaseBundle]:
    user = make_tenant_user("Assessor")
    return user, seed_case(session, user.tenant_id, user.user_id, incidents=3)


def _corroborate(session: Session, case_id: uuid.UUID) -> None:
    case = session.get(Case, case_id)
    assert case is not None
    case.attribution_status = "CORROBORATED"
    session.commit()


def _by_issue(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["issue_type"]: r for r in body["results"]}


def test_bare_case_is_insufficient_information_with_missing_requirements(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    resp = client.post(f"/v1/cases/{bundle.case_id}/assess", json={}, headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ruleset_version"] == CURRENT_RULESET_VERSION
    assert "not legal advice" in body["disclaimer"].lower()
    assert body["ai_commentary"] is None
    results = _by_issue(body)
    assert len(results) == 6
    dnc = results["DNC_REGISTRY"]
    assert dnc["status"] == "INSUFFICIENT_INFORMATION"
    missing = {m["item"] for m in dnc["missing_requirements"]}
    assert {"call_type", "seller_identifiable", "dnc_registry_history", "exemptions"} <= missing
    assert all(m["how_to_resolve"] for m in dnc["missing_requirements"])
    assert dnc["supporting_event_ids"]  # ledger events + incident ids of the linked calls
    for r in results.values():
        assert r["status"] in ALLOWED


def test_fully_supported_facts_give_flags_never_proof(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _corroborate(session, bundle.case_id)
    body = client.post(
        f"/v1/cases/{bundle.case_id}/assess", json={"user_facts": FULL_FACTS}, headers=user.headers
    ).json()
    results = _by_issue(body)
    assert results["DNC_REGISTRY"]["status"] == "POSSIBLY_RELEVANT"
    assert results["DNC_ENTITY_SPECIFIC"]["status"] == "POSSIBLY_RELEVANT"
    assert results["AUTOMATED_OR_PRERECORDED_CALL"]["status"] == "POSSIBLY_RELEVANT"
    assert results["TELEMARKETING_SALES_RULE"]["status"] == "REQUIRES_COUNSEL_REVIEW"
    assert results["STATE_LAW"]["status"] == "REQUIRES_COUNSEL_REVIEW"
    assert results["CALLER_ID_SPOOFING"]["status"] == "INSUFFICIENT_INFORMATION"
    assert find_forbidden(body) == []
    assert body["user_facts_label"] == "USER_ATTESTED"
    session.expire_all()
    case = session.get(Case, bundle.case_id)
    assert case is not None and case.legal_assessment == "POTENTIALLY_RELEVANT"


def test_contradicting_facts_are_not_supported(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _corroborate(session, bundle.case_id)
    facts = {
        **FULL_FACTS,
        "call_type": "INFORMATIONAL",
        "technology": "LIVE_AGENT",
        "dnc_registered_on": "2026-09-30",  # calls fall inside the grace window
        "entity_specific_request_on": "2026-12-01",  # no contact after this date
    }
    results = _by_issue(
        client.post(
            f"/v1/cases/{bundle.case_id}/assess", json={"user_facts": facts}, headers=user.headers
        ).json()
    )
    for issue in ("DNC_REGISTRY", "DNC_ENTITY_SPECIFIC", "AUTOMATED_OR_PRERECORDED_CALL",
                  "TELEMARKETING_SALES_RULE"):  # fmt: skip
        assert results[issue]["status"] == "NOT_SUPPORTED_BY_CURRENT_EVIDENCE", issue
        assert results[issue]["contradictions"]


def test_possible_exemption_and_autodialer_need_counsel(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    _corroborate(session, bundle.case_id)
    facts = {**FULL_FACTS, "exemption_status": "POSSIBLE", "technology": "AUTODIALER"}
    results = _by_issue(
        client.post(
            f"/v1/cases/{bundle.case_id}/assess", json={"user_facts": facts}, headers=user.headers
        ).json()
    )
    assert results["DNC_REGISTRY"]["status"] == "REQUIRES_COUNSEL_REVIEW"
    assert results["AUTOMATED_OR_PRERECORDED_CALL"]["status"] == "REQUIRES_COUNSEL_REVIEW"
    assert results["DNC_REGISTRY"]["counsel_reasons"]


def test_each_evaluation_appends_rows_and_facts_carry_forward(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/assess"
    first = client.post(
        url, json={"user_facts": {"call_type": "TELEMARKETING"}}, headers=user.headers
    )
    second = client.post(
        url, json={"user_facts": {"exemption_status": "NONE_KNOWN"}}, headers=user.headers
    )
    assert first.json()["run_id"] != second.json()["run_id"]
    assert second.json()["user_facts"] == {
        "call_type": "TELEMARKETING",
        "exemption_status": "NONE_KNOWN",
    }
    session.expire_all()
    rows = session.execute(select(CaseAssessment)).scalars().all()
    assert len(rows) == 12
    assert {r.ruleset_version for r in rows} == {CURRENT_RULESET_VERSION}
    assert all(r.status in ALLOWED for r in rows)
    assert all(r.model_version is None for r in rows)
    listing = client.get(f"/v1/cases/{bundle.case_id}/assessments", headers=user.headers).json()
    assert len(listing["runs"]) == 2
    assert listing["runs"][0]["run_id"] == second.json()["run_id"]


def test_idempotency_key_replays_the_same_run(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/assess"
    a = client.post(url, json={"idempotency_key": "k1"}, headers=user.headers).json()
    b = client.post(url, json={"idempotency_key": "k1"}, headers=user.headers).json()
    assert a["run_id"] == b["run_id"] and b["created"] is False
    assert len(session.execute(select(AssessmentRun)).scalars().all()) == 1


def test_no_damages_or_liability_fields_accepted_or_returned(
    client: TestClient, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/assess"
    assert client.post(url, json={"damages": 1500}, headers=user.headers).status_code == 422
    bad_fact = client.post(url, json={"user_facts": {"damages": 1}}, headers=user.headers)
    assert bad_fact.status_code == 422
    body = client.post(url, json={}, headers=user.headers).json()

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    assert not any(("damage" in k or "liab" in k or "amount" in k) for k in keys(body))
    assert find_forbidden(body) == []


def test_model_inferred_label_does_not_establish_call_type(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    cls = Classification(
        tenant_id=user.tenant_id,
        call_id=bundle.session_ids[0],
        model_version="m1",
        label="UNSOLICITED_SALES",
        confidence=0.99,
    )
    session.add(cls)
    session.commit()
    url = f"/v1/cases/{bundle.case_id}/assess"
    first = _by_issue(client.post(url, json={}, headers=user.headers).json())
    item = next(
        m for m in first["DNC_REGISTRY"]["missing_requirements"] if m["item"] == "call_type"
    )
    assert "unreviewed" in item["reason"]
    cls.reviewed_status = "CONFIRMED"
    session.commit()
    second = _by_issue(client.post(url, json={}, headers=user.headers).json())
    assert "call_type" not in {m["item"] for m in second["DNC_REGISTRY"]["missing_requirements"]}


def test_documented_optout_counts_as_request_history_but_acknowledgment_is_not_proof(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    res = ResolutionCase(
        tenant_id=user.tenant_id, protected_line_id=bundle.line_id, enforce_case_id=bundle.case_id
    )
    session.add(res)
    session.flush()
    session.add(OptOutEvent(tenant_id=user.tenant_id, case_id=res.id, status="DRAFTED"))
    session.commit()
    url = f"/v1/cases/{bundle.case_id}/assess"
    drafted = _by_issue(client.post(url, json={}, headers=user.headers).json())
    assert "entity_request_history" in {
        m["item"] for m in drafted["DNC_ENTITY_SPECIFIC"]["missing_requirements"]
    }
    session.add(OptOutEvent(tenant_id=user.tenant_id, case_id=res.id, status="ACKNOWLEDGED"))
    session.commit()
    acked = _by_issue(client.post(url, json={}, headers=user.headers).json())
    missing = {m["item"] for m in acked["DNC_ENTITY_SPECIFIC"]["missing_requirements"]}
    assert "entity_request_history" not in missing


def test_ai_commentary_is_separate_labeled_and_default_none(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    case = session.get(Case, bundle.case_id)
    assert case is not None
    plain, _ = run_assessment(session, case, user.user_id, None, None)
    assert plain.ai_commentary is None and plain.ai_commentary_label is None

    def provider(rows: list[dict[str, Any]]) -> tuple[str, str]:
        return "Consider whether the caller identity can be corroborated.", "model-x"

    run, _ = run_assessment(session, case, user.user_id, None, None, provider)
    assert run.ai_commentary_label == "MODEL_INFERRED" and run.ai_model_version == "model-x"
    assert all("ai" not in r["issue_type"].lower() for r in run.results)
    assert all(r["status"] in ALLOWED for r in run.results)  # commentary cannot change status

    def bad_provider(rows: list[dict[str, Any]]) -> tuple[str, str]:
        return "VIOLATION_PROVEN; expect damages", "model-x"

    dropped, _ = run_assessment(session, case, user.user_id, None, None, bad_provider)
    assert dropped.ai_commentary is None


def test_recorded_audio_without_authorization_requires_counsel(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    from tests.filing_helpers import add_artifact

    user, bundle = world
    _corroborate(session, bundle.case_id)
    add_artifact(session, user.tenant_id, bundle.case_id, source_type="AUDIO_RECORDING",
                 media_type="audio/wav")  # fmt: skip
    results = _by_issue(
        client.post(
            f"/v1/cases/{bundle.case_id}/assess",
            json={"user_facts": FULL_FACTS},
            headers=user.headers,
        ).json()
    )
    assert results["DNC_REGISTRY"]["status"] == "REQUIRES_COUNSEL_REVIEW"
    assert any(
        r["item"] == "recording_authorization" for r in results["DNC_REGISTRY"]["counsel_reasons"]
    )


def test_more_evidence_changes_support_ids_not_vocabulary(
    client: TestClient, session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/assess"
    before = _by_issue(client.post(url, json={}, headers=user.headers).json())
    inc = add_incident(session, user.tenant_id, bundle, 30)
    session.commit()
    after = _by_issue(client.post(url, json={}, headers=user.headers).json())
    ids_before = set(before["STATE_LAW"]["supporting_event_ids"])
    ids_after = set(after["STATE_LAW"]["supporting_event_ids"])
    assert str(inc.id) in ids_after - ids_before


def test_assess_cross_tenant_denied(
    client: TestClient, session: Session, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    bundle = seed_case(session, a.tenant_id, a.user_id)
    r1 = client.post(f"/v1/cases/{bundle.case_id}/assess", json={}, headers=b.headers)
    r2 = client.get(f"/v1/cases/{bundle.case_id}/assessments", headers=b.headers)
    for resp in (r1, r2):
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_FOUND"
    assert session.execute(select(AssessmentRun)).scalars().all() == []
    assert client.post(f"/v1/cases/{bundle.case_id}/assess", json={}).status_code == 401


def test_user_facts_validation(client: TestClient, world: tuple[TenantUser, CaseBundle]) -> None:
    user, bundle = world
    url = f"/v1/cases/{bundle.case_id}/assess"
    assert (
        client.post(
            url, json={"user_facts": {"call_type": "NONSENSE"}}, headers=user.headers
        ).status_code
        == 422
    )
    assert UserFacts().to_json() == {}
