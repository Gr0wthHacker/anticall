from __future__ import annotations

import itertools

from callshield.domain.enums import (
    AssessmentStatus,
    EnforceCaseState,
    ResolutionState,
    SubmissionState,
)
from callshield.domain.state_machines import (
    ENFORCE_TRANSITIONS,
    RESOLUTION_TRANSITIONS,
    Machine,
    allowed_targets,
    can_transition,
    evaluate_transition,
    is_structurally_allowed,
)

R = ResolutionState
E = EnforceCaseState


def test_tables_cover_every_state() -> None:
    assert set(RESOLUTION_TRANSITIONS) == set(ResolutionState)
    assert set(ENFORCE_TRANSITIONS) == set(EnforceCaseState)
    for targets in RESOLUTION_TRANSITIONS.values():
        assert targets <= set(ResolutionState)
    for enforce_targets in ENFORCE_TRANSITIONS.values():
        assert enforce_targets <= set(EnforceCaseState)


def test_closed_is_terminal() -> None:
    assert allowed_targets(Machine.RESOLUTION, R.CLOSED) == frozenset()
    assert allowed_targets(Machine.ENFORCE_CASE, E.CLOSED) == frozenset()


def test_resolution_happy_path_structure() -> None:
    path = [
        R.CREATED,
        R.DESTINATION_REVIEW,
        R.VERIFIED_DESTINATION,
        R.USER_APPROVED_CONTACT,
        R.IN_PROGRESS,
        R.REQUEST_DELIVERED,
        R.ACKNOWLEDGED,
        R.MONITORING,
        R.POSSIBLE_RECONTACT,
        R.ATTRIBUTION_REVIEW,
        R.REOPENED,
    ]
    for a, b in itertools.pairwise(path):
        assert is_structurally_allowed(Machine.RESOLUTION, a, b), (a, b)


def test_resolution_cannot_skip_destination_verification() -> None:
    assert not is_structurally_allowed(Machine.RESOLUTION, R.CREATED, R.IN_PROGRESS)
    assert not is_structurally_allowed(Machine.RESOLUTION, R.DESTINATION_REVIEW, R.IN_PROGRESS)
    assert not is_structurally_allowed(Machine.RESOLUTION, R.VERIFIED_DESTINATION, R.IN_PROGRESS)


def test_acknowledged_is_not_suppression() -> None:
    assert "SUPPRESSED_IN_SENDER_SYSTEM" not in {s.value for s in ResolutionState}
    assert allowed_targets(Machine.RESOLUTION, R.ACKNOWLEDGED) == {
        R.MONITORING,
        R.NEXT_STEP_REVIEW,
        R.CLOSED,
    }


def test_resolution_guards_fail_closed() -> None:
    m = Machine.RESOLUTION
    assert not can_transition(m, R.DESTINATION_REVIEW, R.VERIFIED_DESTINATION)
    verified = {"destination_independently_verified": True}
    assert can_transition(m, R.DESTINATION_REVIEW, R.VERIFIED_DESTINATION, verified)
    assert not can_transition(m, R.USER_APPROVED_CONTACT, R.IN_PROGRESS, {"policy_approved": True})
    ok_ctx = {
        "policy_approved": True,
        "user_authorization_id": "x",
        "destination_independently_verified": True,
    }
    assert can_transition(m, R.USER_APPROVED_CONTACT, R.IN_PROGRESS, ok_ctx)


def test_attribution_review_can_reopen_or_dismiss() -> None:
    assert allowed_targets(Machine.RESOLUTION, R.ATTRIBUTION_REVIEW) == {R.REOPENED, R.NOT_RELATED}


def test_enforce_full_lifecycle_structure() -> None:
    path = [
        E.OPENED,
        E.INTAKE,
        E.EVIDENCE_COLLECTING,
        E.REVIEW_REQUIRED,
        E.ELIGIBLE_TO_DRAFT,
        E.DRAFT_READY,
        E.USER_APPROVED,
        E.SUBMISSION_IN_PROGRESS,
        E.SUBMITTED,
        E.AGENCY_ACKNOWLEDGED,
        E.MONITORING,
        E.SUPPLEMENT_PENDING,
        E.CLOSED,
    ]
    for a, b in itertools.pairwise(path):
        assert is_structurally_allowed(Machine.ENFORCE_CASE, a, b), (a, b)


def test_draft_generation_alone_cannot_mean_submitted() -> None:
    for state in (E.DRAFT_READY, E.ELIGIBLE_TO_DRAFT, E.USER_APPROVED):
        assert not is_structurally_allowed(Machine.ENFORCE_CASE, state, E.SUBMITTED)


def test_user_approved_guard_requires_everything() -> None:
    m = Machine.ENFORCE_CASE
    full = {
        "line_authorized": True,
        "evidence_snapshot_digest": "abc",
        "required_fields_complete": True,
        "attestation_completed": True,
        "sharing_confirmed": True,
        "destination_validated": True,
    }
    assert can_transition(m, E.DRAFT_READY, E.USER_APPROVED, full)
    for key in full:
        partial = {k: v for k, v in full.items() if k != key}
        decision = evaluate_transition(m, E.DRAFT_READY, E.USER_APPROVED, partial)
        assert not decision.allowed
        assert key in decision.reasons[0]


def test_submission_requires_adapter_and_real_ack() -> None:
    m = Machine.ENFORCE_CASE
    assert not can_transition(m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, {})
    manual = {"adapter_method": "manual_handoff"}
    assert not can_transition(m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, manual)
    api_ctx = {"adapter_method": "documented_api", "scoped_credential": True}
    assert can_transition(m, E.USER_APPROVED, E.SUBMISSION_IN_PROGRESS, api_ctx)
    assert not can_transition(m, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, {})
    assert can_transition(m, E.SUBMISSION_IN_PROGRESS, E.SUBMITTED, {"transport_ack": True})
    assert not can_transition(m, E.SUBMITTED, E.AGENCY_ACKNOWLEDGED, {"transport_ack": True})
    receipt = {"agency_confirmation_ref": "ref-1"}
    assert can_transition(m, E.SUBMITTED, E.AGENCY_ACKNOWLEDGED, receipt)


def test_ambiguous_submission_cannot_be_closed_directly() -> None:
    assert not is_structurally_allowed(Machine.ENFORCE_CASE, E.SUBMISSION_IN_PROGRESS, E.CLOSED)


def test_forbidden_vocabulary_absent() -> None:
    assert "VIOLATION_PROVEN" not in {s.value for s in AssessmentStatus}
    assert {"UNKNOWN", "EXTERNAL_SUBMISSION_REQUIRED"} <= {s.value for s in SubmissionState}
