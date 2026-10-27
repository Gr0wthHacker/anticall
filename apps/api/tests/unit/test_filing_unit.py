"""Pure unit and property tests for the filing and assessment layers."""

# ruff: noqa: E501

from __future__ import annotations

import dataclasses
import json
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from callshield.assessment import engine as engine_mod
from callshield.assessment.engine import ALLOWED_STATUSES, IssueResult, evaluate_case
from callshield.assessment.facts import CaseFacts, UserFacts
from callshield.assessment.rulesets import CURRENT_RULESET_VERSION, RULESETS, get_ruleset
from callshield.assessment.safety import find_forbidden
from callshield.assessment.schemas import AssessmentRunOut, IssueOut
from callshield.core.errors import UnprocessableError
from callshield.domain.enums import (
    AdapterMethod,
    AssessmentStatus,
)
from callshield.domain.enums import EnforceCaseState as E
from callshield.domain.state_machines import _GUARDS, Machine, can_transition
from callshield.filing import states
from callshield.filing.adapters import (
    AdapterRegistrationError,
    FilingDraftView,
    ManualHandoffAdapter,
    SubmittingAdapter,
    documented_adapter_for,
    register_documented_adapter,
    reset_adapters,
)
from callshield.filing.attestation import ATTESTATION_V1, ATTESTATION_VERSION
from callshield.filing.destinations import DESTINATION_SEEDS, LAST_REVIEWED
from callshield.filing.selection import ExportScope, artifact_exclusion_reason
from callshield.filing.snapshot import (
    ArtifactFact,
    CaseSnapshot,
    IncidentFact,
    LineFact,
    OptOutFact,
)
from callshield.filing.templates import (
    TEMPLATES,
    build_complaint_text,
    check_completeness,
    validate_user_fields,
)
from tests.filing_helpers import FakeDocumentedApi

REPO_ROOT = Path(__file__).resolve().parents[4]
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_adapters() -> Any:
    reset_adapters()
    yield
    reset_adapters()


# ------------------------------------------------------------------ attestation / registry


def test_attestation_copy_matches_legal_json() -> None:
    legal = json.loads((REPO_ROOT / "docs/legal/filing-attestation-v1.json").read_text("utf-8"))
    assert legal == ATTESTATION_V1
    assert f"{legal['id']}-{legal['version']}" == ATTESTATION_VERSION
    assert legal["per_submission"] is True


def test_all_shipped_destinations_are_manual_handoff() -> None:
    assert len(DESTINATION_SEEDS) == 5
    assert {s.method for s in DESTINATION_SEEDS} == {AdapterMethod.MANUAL_HANDOFF.value}
    assert datetime(2026, 10, 27, tzinfo=UTC) == LAST_REVIEWED
    by_slug = {s.slug: s for s in DESTINATION_SEEDS}
    assert by_slug["state-ag-generic"].validated is False
    assert by_slug["state-ag-generic"].template_key is None
    assert "NOT VALIDATED" in by_slug["state-ag-generic"].capability_evidence
    for seed in DESTINATION_SEEDS:
        assert seed.source_url.startswith("https://") and seed.retention and seed.sunset_procedure
        assert "no documented" in seed.capability_evidence.lower() or "NOT VALIDATED" in (
            seed.capability_evidence
        )
        assert seed.attachment_constraints["audio_allowed"] is False
        if seed.template_key:
            assert seed.template_key in TEMPLATES
    fcc = " ".join(by_slug["fcc-unwanted-call"].notices)
    assert "does not resolve individual unwanted-call complaints" in fcc
    assert "not adjudication" in fcc


def test_manual_handoff_adapter_has_no_submit() -> None:
    adapter = ManualHandoffAdapter("m", "Dest", "https://x.example/", ("step",))
    assert not hasattr(adapter, "submit") and not hasattr(adapter, "lookup_status")
    assert not isinstance(adapter, SubmittingAdapter)
    view = FilingDraftView(uuid.uuid4(), 1, "FTC", "slug", "v", {"complaint_text": "hi"}, "d" * 64)
    assert adapter.validate(view).ok
    prepared = adapter.prepare(view)
    assert prepared.copyable_text == "hi" and prepared.method == "manual_handoff"
    empty = FilingDraftView(uuid.uuid4(), 1, "FTC", "slug", "v", {}, "d" * 64)
    assert not adapter.validate(empty).ok


def test_registry_only_registers_documented_methods() -> None:
    manual = ManualHandoffAdapter("m", "Dest", "https://x.example/", ())
    with pytest.raises(AdapterRegistrationError):
        register_documented_adapter("a", manual, "cred")  # type: ignore[arg-type]
    fake = FakeDocumentedApi()
    with pytest.raises(AdapterRegistrationError):
        register_documented_adapter("a", fake, "")
    fake.method = AdapterMethod.MANUAL_HANDOFF
    with pytest.raises(AdapterRegistrationError):
        register_documented_adapter("a", fake, "cred")
    assert documented_adapter_for("a") is None

    class NoStatus:
        id = "x"
        method = AdapterMethod.DOCUMENTED_API

        def submit(self, submission: Any) -> Any:
            raise AssertionError

    with pytest.raises(AdapterRegistrationError):
        register_documented_adapter("a", NoStatus(), "cred")  # type: ignore[arg-type]
    register_documented_adapter("ok", FakeDocumentedApi(), "cred")
    assert documented_adapter_for("ok") is not None


# ------------------------------------------------------------------ state machines


def test_submission_state_machine_rules() -> None:
    ok = states.submission_transition_allowed
    assert ok("PENDING", "IN_PROGRESS", {"approval_valid": True}).ok
    assert not ok("PENDING", "IN_PROGRESS", {}).ok  # never send without a valid approval
    assert not ok("PENDING", "SUBMITTED", {"transport_ack": True, "transport_ref": "r"}).ok
    assert not ok("IN_PROGRESS", "SUBMITTED", {}).ok
    assert not ok("IN_PROGRESS", "SUBMITTED", {"transport_ack": True}).ok
    assert not ok("IN_PROGRESS", "SUBMITTED", {"transport_ack": True, "transport_ref": " "}).ok
    assert ok("IN_PROGRESS", "SUBMITTED", {"transport_ack": True, "transport_ref": "r"}).ok
    assert ok("IN_PROGRESS", "UNKNOWN", {}).ok
    assert ok("UNKNOWN", "SUBMITTED", {"transport_ack": True, "transport_ref": "r"}).ok
    assert not ok("UNKNOWN", "IN_PROGRESS", {"approval_valid": True}).ok  # no blind retry
    assert not ok("UNKNOWN", "PENDING", {}).ok
    assert not ok("SUBMITTED", "AGENCY_ACKNOWLEDGED", {}).ok
    assert ok("SUBMITTED", "AGENCY_ACKNOWLEDGED", {"agency_confirmation_distinct": True}).ok
    assert not ok("EXTERNAL_SUBMISSION_REQUIRED", states.SUBMITTED_EXTERNALLY, {}).ok
    ext = {"external_submission_attested": True, "receipt_id": "x"}
    assert ok("EXTERNAL_SUBMISSION_REQUIRED", states.SUBMITTED_EXTERNALLY, ext).ok
    assert not ok(
        states.SUBMITTED_EXTERNALLY, "AGENCY_ACKNOWLEDGED", {"agency_confirmation_distinct": True}
    ).ok
    assert not ok("FAILED", "IN_PROGRESS", {"approval_valid": True}).ok


def test_case_guards_fail_closed_and_stricter_than_shared() -> None:
    m = Machine.ENFORCE_CASE
    shared = {
        "line_authorized": True, "evidence_snapshot_digest": "d", "required_fields_complete": True,
        "attestation_completed": True, "sharing_confirmed": True, "destination_validated": True,
    }  # fmt: skip
    mine = {
        "approved_payload_digest": "d",
        "payload_digest_matches": True,
        "snapshot_digest_current": True,
    }
    assert not can_transition(m, E.DRAFT_READY, E.USER_APPROVED, shared)
    assert not can_transition(m, E.DRAFT_READY, E.USER_APPROVED, mine)
    assert can_transition(m, E.DRAFT_READY, E.USER_APPROVED, {**shared, **mine})
    assert not can_transition(m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, {})
    sip = {"adapter_method": "documented_api", "scoped_credential": "c", "approval_valid": True,
           "submission_enabled": True}  # fmt: skip
    assert can_transition(m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, sip)
    assert not can_transition(
        m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, {**sip, "submission_enabled": False}
    )
    assert not can_transition(
        m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, {**sip, "adapter_method": "manual_handoff"}
    )
    # SUBMITTED needs a transport ack WITH a reference; the shared "attested" shortcut is closed here
    assert not can_transition(
        m, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, {"external_submission_attested": True}
    )
    assert not can_transition(m, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, {"transport_ack": True})
    assert can_transition(
        m, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, {"transport_ack": True, "transport_ref": "r"}
    )
    assert not can_transition(
        m, E.EXTERNAL_SUBMISSION_REQUIRED, E.SUBMITTED, {"external_submission_attested": True}
    )
    assert can_transition(m, E.EXTERNAL_SUBMISSION_REQUIRED, E.SUBMITTED,
                          {"external_submission_attested": True, "receipt_id": "r"})  # fmt: skip
    assert not can_transition(
        m, E.SUBMITTED, E.AGENCY_ACKNOWLEDGED, {"agency_confirmation_ref": "x"}
    )
    assert can_transition(m, E.SUBMITTED, E.AGENCY_ACKNOWLEDGED,
                          {"agency_confirmation_ref": "x", "agency_confirmation_distinct": True})  # fmt: skip
    # a draft being generated is never a path to SUBMITTED
    assert not can_transition(
        m, E.DRAFT_READY, E.SUBMITTED, {"transport_ack": True, "transport_ref": "r"}
    )


def test_guard_registration_is_idempotent() -> None:
    def counts() -> dict[Any, int]:
        return {k: len(v) for k, v in _GUARDS.items()}

    before = counts()
    states.register_filing_guards()
    states.register_filing_guards()
    assert counts() == before


# ------------------------------------------------------------------ templates / selection


def _incident(n: int, confirmed: bool = True, number: str | None = "+14155550177") -> IncidentFact:
    return IncidentFact(
        uuid.uuid4(), uuid.uuid4(), T0 + timedelta(days=n), number, "UNSOLICITED_SALES",
        "OBSERVED", "LINKED", "USER_LINK" if confirmed else "AUTO_MATCH",
    )  # fmt: skip


def _snapshot(
    incidents: list[IncidentFact], line_status: str = "VERIFIED", attribution: str = "UNKNOWN",
    opt_outs: tuple[OptOutFact, ...] = (), artifacts: tuple[ArtifactFact, ...] = (),
    audio_auth: str = "NOT_EVALUATED",
) -> CaseSnapshot:  # fmt: skip
    line = LineFact(uuid.uuid4(), "+14155550123", line_status, None)
    return CaseSnapshot(
        uuid.uuid4(), attribution, audio_auth, line, tuple(incidents), (), opt_outs, artifacts, ()
    )


def test_field_validation_and_completeness_checklist() -> None:
    spec = TEMPLATES["fcc_unwanted_call_v1"]
    with pytest.raises(UnprocessableError):
        validate_user_fields(spec, {"payment_made": "NO"})
    with pytest.raises(UnprocessableError):
        validate_user_fields(spec, {"contact_kind": "PIGEON"})
    with pytest.raises(UnprocessableError):
        validate_user_fields(spec, {"narrative": "x" * 5000})
    with pytest.raises(UnprocessableError):
        validate_user_fields(spec, {"narrative": "that is a $5000 damages claim"})
    assert validate_user_fields(spec, {"contact_kind": " LIVE_CALL ", "narrative": ""}) == {
        "contact_kind": "LIVE_CALL"
    }
    snap = _snapshot([_incident(0), _incident(1, confirmed=False)])
    result = check_completeness(spec, snap, list(snap.incidents), {})
    assert not result["complete"]
    assert {m["field"] for m in result["missing"]} == {"narrative", "contact_kind"}
    assert any("suspected" in w for w in result["warnings"])
    assert "percent" not in json.dumps(result).lower() and "score" not in json.dumps(result).lower()
    fields = {"contact_kind": "LIVE_CALL", "narrative": "n" * 25}
    assert check_completeness(spec, snap, list(snap.incidents), fields)["complete"]
    no_line = _snapshot([_incident(0)], line_status="UNVERIFIED")
    assert "complainant_line" in {
        m["field"]
        for m in check_completeness(spec, no_line, list(no_line.incidents), fields)["missing"]
    }
    no_number = _snapshot([_incident(0, number=None)])
    assert "caller_number" in {
        m["field"]
        for m in check_completeness(spec, no_number, list(no_number.incidents), fields)["missing"]
    }


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_templates_render_clean_deterministic_text(key: str) -> None:
    spec = TEMPLATES[key]
    calls = [_incident(n, confirmed=n % 2 == 0) for n in range(60)]
    fields = {"narrative": "Repeated calls offering a loan.", "contact_kind": "LIVE_CALL",
              "payment_made": "NO", "carrier_name": "ExampleCell",
              "dnc_registration_status": "UNKNOWN"}  # fmt: skip
    fields = {
        k: v for k, v in fields.items() if k in {f.name for f in spec.user_fields} | {"narrative"}
    }
    a = build_complaint_text(spec, "+14155550123", calls, ["Acme"], 2, fields)
    b = build_complaint_text(spec, "+14155550123", calls, ["Acme"], 2, fields)
    assert a == b
    assert find_forbidden(a) == []
    assert "and 10 more calls" in a and "unverified" in a and "spoofed" in a
    assert "suspected, not yet confirmed" in a and "claimed, not verified" in a


def test_selection_exclusion_rules() -> None:
    cid = uuid.uuid4()

    def art(**kw: Any) -> ArtifactFact:
        base: dict[str, Any] = dict(
            artifact_id=uuid.uuid4(), source_type="SCREENSHOT", media_type="image/png", sha256="a" * 64,
            evidence_label="USER_ATTESTED", processing_authorization="PERMITTED",
            sharing_authorization="PERMITTED", redaction_state="NONE", consent_id=None, version=1,
            provenance={},
        )  # fmt: skip
        base.update(kw)
        return ArtifactFact(**base)

    open_dest = {"attachments_accepted_via_handoff": True, "allowed_media_types": ["image/png"],
                 "audio_allowed": True}  # fmt: skip
    scope = ExportScope(include_audio=True)
    assert artifact_exclusion_reason(art(), scope, open_dest) is None
    assert (
        artifact_exclusion_reason(art(sharing_authorization="DENIED"), scope, open_dest)
        == "SHARING_NOT_AUTHORIZED"
    )
    assert (
        artifact_exclusion_reason(art(media_type="image/gif"), scope, open_dest)
        == "NOT_SUPPORTED_BY_DESTINATION"
    )
    third = art(provenance={"contains_third_party_speech": True})
    assert (
        artifact_exclusion_reason(third, scope, open_dest) == "THIRD_PARTY_CONTENT_WITHOUT_CONSENT"
    )
    audio = art(source_type="AUDIO_RECORDING", media_type="audio/wav")
    assert artifact_exclusion_reason(audio, ExportScope(), open_dest) == "AUDIO_NOT_IN_EXPORT_SCOPE"
    assert artifact_exclusion_reason(audio, scope, open_dest) == "UNCONSENTED_AUDIO"
    consented = art(source_type="AUDIO_RECORDING", media_type="audio/wav", consent_id=cid)
    open_dest["allowed_media_types"] = []
    assert artifact_exclusion_reason(consented, scope, open_dest) is None
    assert artifact_exclusion_reason(consented, scope, {**open_dest, "audio_allowed": False}) == (
        "NOT_SUPPORTED_BY_DESTINATION"
    )
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError: scope is explicit-only
        ExportScope.model_validate({"include_third_party_names": True})


# ------------------------------------------------------------------ rulesets & engine


def test_ruleset_is_versioned_and_complete() -> None:
    rs = get_ruleset()
    assert rs["ruleset_version"] == CURRENT_RULESET_VERSION in RULESETS
    assert rs["counsel_reviewed"] is False and "not legal advice" in rs["disclaimer"].lower()
    for item in rs["checklist"]:
        assert item in engine_mod.EVALUATORS and item in rs["how_to_resolve"]
    for issue, spec in rs["issue_types"].items():
        assert set(spec["requires"]) <= set(rs["checklist"]), issue
        assert set(spec["counsel_if"]) <= set(rs["checklist"]), issue
    with pytest.raises(KeyError):
        get_ruleset("nope")
    required = {"line_control", "call_dates_times", "call_type", "seller_identifiable",
                "dnc_registry_history", "entity_request_history", "later_communications",
                "technology_type", "recording_authorization", "jurisdiction", "exemptions"}  # fmt: skip
    assert required <= set(rs["checklist"])
    assert find_forbidden(rs) == []


def _random_facts(rng: random.Random) -> CaseFacts:
    n = rng.randint(0, 4)
    incidents = [_incident(rng.randint(0, 90)) for _ in range(n)]
    if n and rng.random() < 0.2:
        incidents.append(dataclasses.replace(incidents[0], occurred_at=None))
    opt_outs = tuple(
        OptOutFact(uuid.uuid4(), rng.choice(["DRAFTED", "DELIVERED", "ACKNOWLEDGED", "REFUSED"]),
                   "CLAIMED", None, T0 + timedelta(days=rng.randint(0, 60)))
        for _ in range(rng.randint(0, 2))
    )  # fmt: skip
    snap = _snapshot(
        incidents, rng.choice(["VERIFIED", "UNVERIFIED", "REVOKED"]),
        rng.choice(["UNKNOWN", "CANDIDATE", "CORROBORATED", "DISPUTED"]), opt_outs,
        audio_auth=rng.choice(["NOT_EVALUATED", "PERMITTED", "DENIED"]),
    )  # fmt: skip
    if rng.random() < 0.1:
        snap = dataclasses.replace(snap, line=None)
    user = UserFacts(
        call_type=rng.choice([None, "TELEMARKETING", "INFORMATIONAL", "DEBT_COLLECTION", "EXEMPT", "UNKNOWN"]),
        technology=rng.choice([None, "LIVE_AGENT", "PRERECORDED", "ARTIFICIAL_VOICE", "AUTODIALER", "UNKNOWN"]),
        caller_jurisdiction=rng.choice([None, "NY"]),
        called_party_state=rng.choice([None, "CA"]),
        dnc_registered_on=rng.choice([None, date(2026, 6, 1), date(2026, 9, 30), date(2027, 1, 1)]),
        entity_specific_request_on=rng.choice([None, date(2026, 9, 1), date(2026, 12, 31)]),
        exemption_status=rng.choice([None, "NONE_KNOWN", "POSSIBLE", "CLAIMED", "UNKNOWN"]),
        caller_id_inaccurate=rng.choice([None, True, False]),
    )  # fmt: skip
    return CaseFacts(
        snap, user,
        confirmed_labels=frozenset(rng.sample(["UNSOLICITED_SALES", "SURVEY"], rng.randint(0, 1))),
        inferred_labels=frozenset(rng.sample(["UNSOLICITED_SALES"], rng.randint(0, 1))),
        attestation_metadata_seen=rng.random() < 0.5,
    )  # fmt: skip


def test_property_outputs_always_in_allowed_set_and_deterministic() -> None:
    rs = get_ruleset()
    rng = random.Random(20261027)
    seen: set[str] = set()
    for _ in range(600):
        facts = _random_facts(rng)
        out = evaluate_case(facts, rs)
        assert [r.issue_type for r in out] == list(rs["issue_types"])
        for res in out:
            assert res.status.value in ALLOWED_STATUSES
            assert res.status in AssessmentStatus
            seen.add(res.status.value)
            if res.status is AssessmentStatus.INSUFFICIENT_INFORMATION:
                assert res.missing_requirements
            if res.status is AssessmentStatus.NOT_SUPPORTED_BY_CURRENT_EVIDENCE:
                assert res.contradictions
            if res.status is AssessmentStatus.POSSIBLY_RELEVANT:
                assert not (res.missing_requirements or res.contradictions or res.counsel_reasons)
            assert find_forbidden(res.to_json()) == []
        assert [r.to_json() for r in out] == [r.to_json() for r in evaluate_case(facts, rs)]
    assert seen == ALLOWED_STATUSES  # the generator exercises every output state


def test_property_no_damages_or_liability_fields_exist_anywhere() -> None:
    banned = ("damage", "liab", "amount", "owed", "award", "payout", "violation_proven")
    names: set[str] = {f.name for f in dataclasses.fields(IssueResult)}
    names |= set(IssueOut.model_fields) | set(AssessmentRunOut.model_fields)
    names |= set(UserFacts.model_fields)
    assert not [n for n in names if any(b in n.lower() for b in banned)]
    assert "VIOLATION_PROVEN" not in {s.value for s in AssessmentStatus}
    assert len(AssessmentStatus) == 4
    assert find_forbidden("This is a VIOLATION_PROVEN case") and find_forbidden(
        "statutory damages $500"
    )
    assert find_forbidden("no money is mentioned here") == []


def test_registry_grace_window_uses_ruleset_parameter() -> None:
    rs = get_ruleset()
    snap = _snapshot([_incident(0)])  # call on T0 = 2026-10-01
    base = CaseFacts(snap, UserFacts(call_type="TELEMARKETING", exemption_status="NONE_KNOWN"),
                     confirmed_labels=frozenset(), inferred_labels=frozenset())  # fmt: skip

    def status(registered: date) -> str:
        facts = dataclasses.replace(
            base, user=base.user.model_copy(update={"dnc_registered_on": registered}),
            snapshot=dataclasses.replace(snap, attribution_status="CORROBORATED"),
        )  # fmt: skip
        return next(
            r for r in evaluate_case(facts, rs) if r.issue_type == "DNC_REGISTRY"
        ).status.value

    assert status(date(2026, 8, 31)) == "POSSIBLY_RELEVANT"  # 31 days before
    assert status(date(2026, 9, 1)) == "NOT_SUPPORTED_BY_CURRENT_EVIDENCE"  # 30 days: inside window
