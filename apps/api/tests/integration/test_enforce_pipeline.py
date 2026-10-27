from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.casebuilder import (
    UserAccount,
    build_timeline,
    import_user_accounts,
    review_attribution,
    run_incident_pipeline,
    transition_case,
)
from callshield.casebuilder.config import DEFAULT_CONFIG
from callshield.casebuilder.linking import (
    all_link_rows,
    effective_label,
    linked_incident_ids,
    reclassify_incident,
    set_link,
)
from callshield.casebuilder.optout import recompute_optout_timeline
from callshield.casebuilder.pipeline import PipelineResult, persist_call_audio
from callshield.casebuilder.policy import check_recording_policy
from callshield.casebuilder.recompute import recompute_case
from callshield.casebuilder.snapshot import latest_snapshot
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.db.models import (
    CallSession,
    Case,
    CaseIncident,
    EvidenceArtifact,
    Incident,
    LedgerEvent,
    Tenant,
)
from callshield.db.models.enforce_ext import CaseReviewItem
from callshield.db.session import make_session_factory
from callshield.domain.enums import (
    AttributionStatus,
    EnforceCaseState,
    EventOrigin,
    ProcessingMode,
)
from callshield.domain.topics import Topic
from callshield.evidence.store import LocalArtifactStore
from callshield.ledger import append_event
from callshield.worker.queue import enqueue
from callshield.worker.runner import load_handler_modules, run_one
from tests.enforce_support import (
    CALLER_A,
    seed_consent,
    seed_incident,
    seed_line,
    seed_opt_out,
    seed_resolution,
)

pytest_plugins = ("tests.enforce_support",)


def _tenant(session: Session) -> Tenant:
    tenant = Tenant(name="T")
    session.add(tenant)
    session.flush()
    return tenant


def _run(
    session: Session, store: LocalArtifactStore, settings: Settings, tenant: Tenant, inc: Incident
) -> PipelineResult:
    return run_incident_pipeline(session, tenant.id, inc.id, store=store, settings=settings)


def _count(session: Session, model: type, *where: Any) -> int:
    return session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def _alerts(session: Session) -> int:
    return _count(session, LedgerEvent, LedgerEvent.topic == "case.alert")


def test_first_unwanted_call_opens_a_candidate_case(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incident = seed_incident(session, tenant.id, line.id, claimed="Acme Lending LLC")
    result = _run(session, store, app_settings, tenant, incident)
    assert result.action == "CASE_OPENED" and result.case_created and result.case_id
    case = session.get(Case, result.case_id)
    assert case is not None
    assert case.protected_line_id == line.id and case.state == EnforceCaseState.INTAKE.value
    assert case.attribution_status == "UNKNOWN"  # one call proves nothing about the operator
    assert linked_incident_ids(session, tenant.id, case.id) == [incident.id]
    rows = all_link_rows(session, tenant.id, case.id)
    assert rows[0].linkage_type == "AUTO_MATCH"
    assert rows[0].evidence_basis["match_ruleset_version"] == DEFAULT_CONFIG.match_ruleset_version
    assert rows[0].evidence_basis["same_displayed_number_is_not_identity"] is True
    assert CALLER_A not in str(rows[0].evidence_basis)  # masked, never the raw number
    assert result.alerted and _alerts(session) == 1
    snapshot = latest_snapshot(session, tenant.id, case.id)
    assert snapshot is not None and snapshot.meta["linked_incident_count"] == 1


def test_pipeline_runs_through_the_job_queue_and_is_idempotent(
    session: Session, engine: Engine, store: LocalArtifactStore
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incident = seed_incident(session, tenant.id, line.id)
    for _ in range(2):  # a duplicate delivery of the same job
        _, _created = enqueue(
            session, tenant.id, "incident.created", {"incident_id": str(incident.id)},
            idempotency_key=f"incident:{incident.id}",
        )  # fmt: skip
    session.commit()
    load_handler_modules()
    factory = make_session_factory(engine)
    assert run_one(factory, ["incident.created"]) is True
    assert run_one(factory, ["incident.created"]) is False  # only one job existed
    session.expire_all()
    assert _count(session, Case) == 1
    # Re-running the pipeline itself is also a no-op.
    again = run_incident_pipeline(session, tenant.id, incident.id, store=store)
    assert again.action == "ALREADY_LINKED" and not again.alerted
    assert _count(session, CaseIncident) == 1
    assert _count(session, EvidenceArtifact) == 1
    assert _alerts(session) == 1


def test_duplicate_events_create_one_observed_call(session: Session) -> None:
    tenant = _tenant(session)
    payload = {"provider_call_id": "CA1"}
    _, first = append_event(
        session, tenant.id, Topic.CALL_RECEIVED, payload, EventOrigin.PROVIDER_WEBHOOK, "k-call-1"
    )
    _, second = append_event(
        session, tenant.id, Topic.CALL_RECEIVED, payload, EventOrigin.PROVIDER_WEBHOOK, "k-call-1"
    )
    assert (first, second) == (True, False)
    line = seed_line(session, tenant.id)
    seed_incident(session, tenant.id, line.id, provider_call_id="CA-DUP")
    with pytest.raises(IntegrityError):
        seed_incident(session, tenant.id, line.id, provider_call_id="CA-DUP")
    session.rollback()


def test_no_consent_persists_no_audio_but_keeps_signaling_metadata(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incident = seed_incident(session, tenant.id, line.id)
    result = _run(session, store, app_settings, tenant, incident)
    assert result.recording_authorization == "NOT_EVALUATED" and result.audio_persisted is False
    case = session.get(Case, result.case_id)
    assert case is not None and case.recording_authorization == "NOT_EVALUATED"
    assert (
        persist_call_audio(
            session, store, app_settings, tenant.id, incident.id, b"RIFF....", "audio/wav"
        )
        is None
    )
    arts = session.execute(select(EvidenceArtifact)).scalars().all()
    assert [a.source_type for a in arts] == ["SIGNALING_METADATA"]
    meta = arts[0]
    assert meta.evidence_label == "OBSERVED" and meta.origin == "ORIGINAL"
    assert meta.provenance["audio_persisted"] is False
    assert meta.storage_key and b"provider_call_id" in store.get(meta.storage_key)
    assert sum(1 for p in store.root.rglob("*") if p.is_file()) == 1  # nothing but metadata


@pytest.mark.parametrize(
    ("mode", "authorized", "revoked", "expected"),
    [
        (ProcessingMode.METADATA_ONLY, True, False, "DENIED"),
        (ProcessingMode.LIVE_STT_TRANSIENT, True, False, "DENIED"),
        (ProcessingMode.RECORDING_RETAINED, False, False, "DENIED"),
        (ProcessingMode.RECORDING_RETAINED, True, True, "DENIED"),
        (ProcessingMode.RECORDING_RETAINED, True, False, "PERMITTED"),
    ],
)
def test_recording_policy_fails_closed(
    session: Session, store: LocalArtifactStore, app_settings: Settings,
    mode: ProcessingMode, authorized: bool, revoked: bool, expected: str,
) -> None:  # fmt: skip
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incident = seed_incident(session, tenant.id, line.id)
    seed_consent(session, tenant.id, line.id, mode, authorized, revoked)
    policy = check_recording_policy(session, tenant.id, line.id, incident.call_session_id)
    assert policy.authorization.value == expected
    stored = persist_call_audio(
        session, store, app_settings, tenant.id, incident.id, b"RIFF", "audio/wav"
    )
    assert (stored is not None) is (expected == "PERMITTED" and mode.value == "RECORDING_RETAINED")


def test_same_displayed_number_with_conflicting_claims_goes_to_review(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = seed_incident(session, tenant.id, line.id, claimed="Acme Lending", offset_minutes=0)
    second = seed_incident(session, tenant.id, line.id, claimed="Bright Solar", offset_minutes=60)
    r1 = _run(session, store, app_settings, tenant, first)
    r2 = _run(session, store, app_settings, tenant, second)
    assert r2.action == "REVIEW_QUEUED" and r2.case_id is None
    assert linked_incident_ids(session, tenant.id, r1.case_id) == [first.id]  # type: ignore[arg-type]
    item = session.get(CaseReviewItem, r2.review_item_id)
    assert item is not None and item.reason == "same_cli_different_claim"
    assert item.candidate_case_ids == [str(r1.case_id)]
    again = _run(session, store, app_settings, tenant, second)  # no duplicate queue entries
    assert again.review_item_id == item.id and _count(session, CaseReviewItem) == 1


def test_spoofed_cli_never_corroborates_or_verifies_repeat_contact(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incidents = [
        seed_incident(session, tenant.id, line.id, offset_minutes=m * 600) for m in range(5)
    ]
    results = [_run(session, store, app_settings, tenant, i) for i in incidents]
    case_id = results[0].case_id
    assert case_id and {r.case_id for r in results} == {case_id}  # grouped by number
    case = session.get(Case, case_id)
    assert case is not None
    assert case.attribution_status == AttributionStatus.CANDIDATE.value
    timeline = recompute_optout_timeline(session, tenant.id, case)
    assert timeline.repeat_contact_verified is False
    config = DEFAULT_CONFIG
    with pytest.raises(UnprocessableError) as excinfo:  # a human cannot corroborate on the number
        review_attribution(
            session, store, tenant.id, case_id, AttributionStatus.CORROBORATED, "same number",
            "user:rev", config,
        )  # fmt: skip
    assert excinfo.value.code == "INSUFFICIENT_INDEPENDENT_FEATURES"
    recompute_case(session, store, tenant.id, case_id, config)
    assert case.attribution_status != AttributionStatus.CORROBORATED.value


def test_corroboration_requires_independent_features_and_a_human(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    incidents = [
        seed_incident(session, tenant.id, line.id, claimed="Acme Lending", offset_minutes=m)
        for m in (0, 20, 40)
    ]  # same claim + clustered timing: two independent features
    results = [_run(session, store, app_settings, tenant, i) for i in incidents]
    case = session.get(Case, results[0].case_id)
    assert case is not None
    assert case.attribution_status == "CANDIDATE"  # the pipeline itself never corroborates
    review_attribution(
        session, store, tenant.id, case.id, AttributionStatus.CORROBORATED, "claims + timing",
        "user:rev", DEFAULT_CONFIG,
    )  # fmt: skip
    assert case.attribution_status == "CORROBORATED"
    recompute_case(session, store, tenant.id, case.id, DEFAULT_CONFIG)
    assert case.attribution_status == "CORROBORATED"
    # Removing support demotes it back for re-review.
    for inc in incidents[1:]:
        set_link(session, tenant.id, case.id, inc.id, link=False, reviewer=False,
                 evidence_basis={"reason": "wrong"}, actor="user:u")  # fmt: skip
    recompute_case(session, store, tenant.id, case.id, DEFAULT_CONFIG)
    assert case.attribution_status == "CANDIDATE"


def test_non_unwanted_calls_open_no_case_but_stay_on_record(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    trusted = seed_incident(session, tenant.id, line.id, label="TRUSTED")
    result = _run(session, store, app_settings, tenant, trusted)
    assert result.action == "NO_CASE" and _count(session, Case) == 0
    assert _count(session, Incident) == 1 and _count(session, CallSession) == 1


def test_separate_lines_get_separate_cases(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line_a = seed_line(session, tenant.id)
    line_b = seed_line(session, tenant.id, e164="+14155550124")
    ra = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line_a.id))
    rb = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line_b.id))
    assert ra.case_id != rb.case_id and _count(session, Case) == 2


def test_user_unlink_is_append_only_and_never_relinked(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    inc = seed_incident(session, tenant.id, line.id, label="UNSOLICITED_SALES")
    label_before = inc.label
    result = _run(session, store, app_settings, tenant, inc)
    case_id = result.case_id
    assert case_id
    why: dict[str, object] = {"reason": "not the same caller"}
    row, changed = set_link(
        session, tenant.id, case_id, inc.id, link=False, reviewer=False,
        evidence_basis=why, actor="user:u",
    )  # fmt: skip
    assert changed and row is not None and row.linkage_type == "USER_UNLINK"
    assert linked_incident_ids(session, tenant.id, case_id) == []
    again = _run(session, store, app_settings, tenant, inc)
    assert (
        again.action == "UNLINK_RESPECTED"
        and linked_incident_ids(session, tenant.id, case_id) == []
    )
    set_link(session, tenant.id, case_id, inc.id, link=True, reviewer=False,
             evidence_basis={}, actor="user:u")  # fmt: skip
    rows = all_link_rows(session, tenant.id, case_id)
    assert [r.linkage_type for r in rows] == ["AUTO_MATCH", "USER_UNLINK", "USER_LINK"]
    assert linked_incident_ids(session, tenant.id, case_id) == [inc.id]  # latest row wins
    assert inc.label == label_before and session.get(Incident, inc.id) is inc  # source untouched
    _, noop = set_link(session, tenant.id, case_id, inc.id, link=True, reviewer=False,
                       evidence_basis={}, actor="user:u")  # fmt: skip
    assert noop is False and len(all_link_rows(session, tenant.id, case_id)) == 3


def test_link_to_incident_on_other_line_is_rejected(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line_a = seed_line(session, tenant.id)
    line_b = seed_line(session, tenant.id, e164="+14155550124")
    result = _run(
        session, store, app_settings, tenant, seed_incident(session, tenant.id, line_a.id)
    )
    foreign = seed_incident(session, tenant.id, line_b.id)
    with pytest.raises(UnprocessableError):
        set_link(session, tenant.id, result.case_id, foreign.id, link=True,  # type: ignore[arg-type]
                 reviewer=False, evidence_basis={}, actor="user:u")  # fmt: skip


def test_reclassification_is_append_only_and_changes_pipeline_outcome(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    inc = seed_incident(session, tenant.id, line.id, label="UNKNOWN")
    assert _run(session, store, app_settings, tenant, inc).action == "NO_CASE"
    r1 = reclassify_incident(
        session, tenant.id, inc.id, "SCAM_SUSPECTED", "it was a scam", "user:u"
    )
    assert effective_label(session, tenant.id, inc) == "SCAM_SUSPECTED" and inc.label == "UNKNOWN"
    assert r1.previous_label == "UNKNOWN"
    assert _run(session, store, app_settings, tenant, inc).action == "CASE_OPENED"
    r2 = reclassify_incident(session, tenant.id, inc.id, "INFORMATIONAL", "actually fine", "user:u")
    assert r2.previous_label == "SCAM_SUSPECTED"
    assert effective_label(session, tenant.id, inc) == "INFORMATIONAL" and inc.label == "UNKNOWN"
    with pytest.raises(UnprocessableError):
        reclassify_incident(session, tenant.id, inc.id, "NOT_A_LABEL", "", "user:u")


def test_eleven_manual_attempts_are_eleven_timeline_entries(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    result = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    assert result.case_id
    seed_resolution(session, tenant.id, line.id, result.case_id, attempts=11)
    entries = build_timeline(session, tenant.id, result.case_id)
    attempts = [e for e in entries if e.kind == "CONTACT_ATTEMPT"]
    assert len(attempts) == 11  # no cutoff, no aggregation
    assert len({e.entry_id for e in attempts}) == 11
    assert [e.occurred_at for e in attempts] == sorted(e.occurred_at for e in attempts)
    assert {e.source_label.value for e in attempts} == {
        "USER_ATTESTED"
    }  # no call session: not observed


def test_timeline_labels_sources_and_ordering_are_stable(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    assert first.case_id
    resolution = seed_resolution(
        session, tenant.id, line.id, first.case_id, attempts=2, with_calls=True
    )
    seed_opt_out(session, tenant.id, resolution.id, "ACKNOWLEDGED")
    import_user_accounts(
        session, store, app_settings, tenant.id, first.case_id,
        [UserAccount("I called eleven times before they stopped", approximate_count=11,
                     category="RETURN_CALL_ATTEMPT")],
        "user:u",
    )  # fmt: skip
    entries = build_timeline(session, tenant.id, first.case_id)
    assert entries == build_timeline(session, tenant.id, first.case_id)  # stable
    assert [e.sort_key() for e in entries] == sorted(e.sort_key() for e in entries)
    by_kind = {e.kind: e for e in entries}
    assert by_kind["INCIDENT"].source_label.value == "OBSERVED"
    assert by_kind["OPT_OUT_EVENT"].source_label.value == "CLAIMED"
    assert "not suppression" in by_kind["OPT_OUT_EVENT"].summary
    assert by_kind["USER_IMPORT"].source_label.value == "USER_ATTESTED"
    assert by_kind["LINK_CHANGE"].source_label.value == "MODEL_INFERRED"
    assert any(e.kind == "LEDGER_EVENT" and e.details["topic"] == "case.created" for e in entries)
    observed_attempts = [e for e in entries if e.kind == "CONTACT_ATTEMPT"]
    assert {e.source_label.value for e in observed_attempts} == {"OBSERVED"}
    assert CALLER_A not in " ".join(e.summary for e in entries)  # masked in summaries
    assert all(e.occurred_at.tzinfo is not None for e in entries)


def test_user_attested_import_fabricates_nothing(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    from datetime import date

    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    assert first.case_id
    created = import_user_accounts(
        session, store, app_settings, tenant.id, first.case_id,
        [
            UserAccount("They called a lot in January", stated_event_date=date(2026, 1, 15)),
            UserAccount("Undated recollection"),
        ],
        "user:u",
    )  # fmt: skip
    for art in created:
        assert (art.origin, art.evidence_label) == ("USER_STATED", "USER_ATTESTED")
        assert art.source_type == "USER_STATEMENT" and art.collection_method == "user_import"
        assert art.provenance["no_observed_metadata"] is True
        assert art.sharing_authorization == "NOT_EVALUATED"
    assert created[0].provenance["stated_event_date"] == "2026-01-15"
    assert created[1].provenance["stated_event_date"] is None
    assert created[1].provenance["date_basis"] == "unknown"
    imports = [
        e for e in build_timeline(session, tenant.id, first.case_id) if e.kind == "USER_IMPORT"
    ]
    dated = next(e for e in imports if e.time_basis == "user_stated_date_only")
    assert dated.occurred_at.date() == date(2026, 1, 15)
    undated = next(e for e in imports if e.time_basis == "recorded")
    assert undated.occurred_at == created[1].collected_at  # recorded time, not an invented date
    assert not any(a.source_type in ("AUDIO_RECORDING", "AI_TRANSCRIPT")
                   for a in session.execute(select(EvidenceArtifact)).scalars())  # fmt: skip


def test_lifecycle_early_states_and_guards(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    from callshield.casebuilder.service import open_case

    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    case, _ = open_case(session, tenant.id, line.id, "manual", actor="user:u")
    assert case.state == "OPENED"
    transition_case(session, tenant.id, case.id, EnforceCaseState.INTAKE, "user:u")
    with pytest.raises(ConflictError):  # needs a linked incident
        transition_case(session, tenant.id, case.id, EnforceCaseState.EVIDENCE_COLLECTING, "user:u")
    inc = seed_incident(session, tenant.id, line.id)
    set_link(session, tenant.id, case.id, inc.id, link=True, reviewer=False,
             evidence_basis={}, actor="user:u")  # fmt: skip
    version = case.review_version
    transition_case(session, tenant.id, case.id, EnforceCaseState.EVIDENCE_COLLECTING, "user:u",
                    expected_review_version=version)  # fmt: skip
    with pytest.raises(ConflictError) as stale:
        transition_case(session, tenant.id, case.id, EnforceCaseState.REVIEW_REQUIRED, "user:u",
                        expected_review_version=version)  # fmt: skip
    assert stale.value.code == "STALE_REVIEW_VERSION"
    transition_case(session, tenant.id, case.id, EnforceCaseState.REVIEW_REQUIRED, "user:u")
    assert case.state == "REVIEW_REQUIRED"
    topics = [e.topic for e in session.execute(select(LedgerEvent)).scalars()]
    assert topics.count("case.state_changed") == 3


def test_user_facing_transitions_cannot_skip_to_filing_states(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    result = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    assert result.case_id
    forged = {
        "line_authorized": True,
        "evidence_snapshot_digest": "x",
        "attestation_completed": True,
    }
    for target in (EnforceCaseState.USER_APPROVED, EnforceCaseState.SUBMITTED,
                   EnforceCaseState.ELIGIBLE_TO_DRAFT):  # fmt: skip
        with pytest.raises(ConflictError):
            transition_case(session, tenant.id, result.case_id, target, "user:u",
                            context=forged, user_facing=True)  # fmt: skip
    with pytest.raises(ConflictError):  # structurally invalid even for trusted callers
        transition_case(session, tenant.id, result.case_id, EnforceCaseState.SUBMITTED, "system",
                        context={"transport_ack": True})  # fmt: skip


def test_optout_timeline_never_asserts_contact_stopped(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    assert first.case_id
    case = session.get(Case, first.case_id)
    assert case is not None
    resolution = seed_resolution(session, tenant.id, line.id, case.id, attempts=1)
    seed_opt_out(session, tenant.id, resolution.id, "ACKNOWLEDGED")
    later = seed_incident(session, tenant.id, line.id, offset_minutes=60 * 24 * 10)
    _run(session, store, app_settings, tenant, later)
    timeline = recompute_optout_timeline(session, tenant.id, case)
    assert timeline.acknowledged and case.case_resolution == "REQUEST_ACKNOWLEDGED"
    assert timeline.possible_subsequent_incident_ids == [later.id]
    assert timeline.repeat_contact_verified is False  # unverified until human corroboration
    assert case.case_resolution != "CONTACT_STOPPED"


def test_alerts_only_on_material_change(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = _run(session, store, app_settings, tenant, seed_incident(session, tenant.id, line.id))
    case_id = first.case_id
    assert case_id and _alerts(session) == 1
    assert not recompute_case(session, store, tenant.id, case_id, DEFAULT_CONFIG).alerted
    assert _alerts(session) == 1
    _run(
        session,
        store,
        app_settings,
        tenant,
        seed_incident(session, tenant.id, line.id, offset_minutes=30),
    )
    assert _alerts(session) == 2
    resolution = seed_resolution(session, tenant.id, line.id, case_id, attempts=0)
    seed_opt_out(session, tenant.id, resolution.id, "DELIVERED")
    refreshed = recompute_case(session, store, tenant.id, case_id, DEFAULT_CONFIG)
    assert refreshed.alerted and "opt_out_event_count" in refreshed.changes
    assert not recompute_case(session, store, tenant.id, case_id, DEFAULT_CONFIG).alerted
    assert _alerts(session) == 3


def test_case_level_recording_authorization_is_conservative(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    line = seed_line(session, tenant.id)
    first = seed_incident(session, tenant.id, line.id, offset_minutes=0)
    seed_consent(session, tenant.id, line.id, ProcessingMode.RECORDING_RETAINED)
    r1 = _run(session, store, app_settings, tenant, first)
    case = session.get(Case, r1.case_id)
    assert case is not None and case.recording_authorization == "PERMITTED"
    seed_consent(session, tenant.id, line.id, ProcessingMode.METADATA_ONLY)
    _run(
        session,
        store,
        app_settings,
        tenant,
        seed_incident(session, tenant.id, line.id, offset_minutes=5),
    )
    assert case.recording_authorization == "DENIED"  # one non-permitted call lowers the case value
