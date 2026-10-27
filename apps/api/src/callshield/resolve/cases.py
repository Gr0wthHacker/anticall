"""Resolution cases, destination registry with provenance, corrections (R-001, R-002)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get_or_404, scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, NotFoundError, UnprocessableError
from callshield.core.phone import InvalidPhoneNumberError, mask_phone, normalize_e164
from callshield.db.models.defend import CallSession, ProtectedLine
from callshield.db.models.resolve import (
    ContactAuthorization,
    ContactDestination,
    ResolutionCase,
)
from callshield.domain.enums import (
    DestinationProvenance,
    ResolutionState,
    Urgency,
    VerificationStatus,
)
from callshield.domain.topics import Topic
from callshield.resolve import machine
from callshield.resolve.events import add_timeline, emit
from callshield.resolve.policy import DestinationFacts

R = ResolutionState
_CORRECTABLE = frozenset({"claimed_entity", "objective", "summary", "policy_region", "urgency"})
_VERIFIABLE_KINDS = frozenset(
    {"OFFICIAL_WEBSITE", "PRIOR_AUTHENTICATED_CORRESPONDENCE", "OTHER_CORROBORATED_SOURCE"}
)
_PRE_CONTACT_STATES = frozenset(
    {
        R.CREATED,
        R.DESTINATION_REVIEW,
        R.VERIFIED_DESTINATION,
        R.USER_APPROVED_CONTACT,
        R.NEXT_STEP_REVIEW,
        R.REOPENED,
        R.ACKNOWLEDGED,
        R.UNCONFIRMED,
        R.REFUSED,
        R.INTERRUPTED,
        R.MONITORING,
    }
)


@dataclass
class NewCase:
    line_id: uuid.UUID
    claimed_entity: str = ""
    objective: str = ""
    summary: str = ""
    urgency: Urgency = Urgency.NORMAL
    policy_region: str = ""
    call_session_ids: list[uuid.UUID] = field(default_factory=list)


def get_case(session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID) -> ResolutionCase:
    return scoped_get_or_404(session, ResolutionCase, case_id, tenant_id)


def get_destination(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID, destination_id: uuid.UUID
) -> ContactDestination:
    dest = scoped_get_or_404(session, ContactDestination, destination_id, tenant_id)
    if dest.case_id != case_id:
        raise NotFoundError("ContactDestination not found", code="NOT_FOUND")
    return dest


def create_case(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, data: NewCase
) -> ResolutionCase:
    """R-001: from call records and/or a manual description. Owner = creating user."""
    line = scoped_get_or_404(session, ProtectedLine, data.line_id, tenant_id)
    if not (data.call_session_ids or data.summary.strip() or data.objective.strip()):
        raise UnprocessableError(
            "provide call records or a description of the problem", code="CASE_NEEDS_SOURCE"
        )
    for call_id in data.call_session_ids:
        call = scoped_get_or_404(session, CallSession, call_id, tenant_id)
        if call.line_id != line.id:
            raise UnprocessableError(
                "call record belongs to another line", code="CALL_LINE_MISMATCH"
            )
    case = ResolutionCase(
        tenant_id=tenant_id,
        protected_line_id=line.id,
        owner_user_id=user_id,
        claimed_entity=data.claimed_entity,
        objective=data.objective,
        summary=data.summary,
        urgency=data.urgency.value,
        policy_region=data.policy_region.strip(),
        status=R.CREATED.value,
    )
    session.add(case)
    session.flush()
    actor = actor_for_user(user_id)
    payload = {
        "case_id": str(case.id),
        "call_session_ids": [str(c) for c in data.call_session_ids],
        "claimed_entity_status": "UNVERIFIED",
        "source": "calls" if data.call_session_ids else "manual",
    }
    add_timeline(session, tenant_id, case.id, "case.created", actor, payload)
    emit(session, tenant_id, Topic.CASE_CREATED, payload, ("case.created", case.id))
    record_audit(session, tenant_id, actor, "resolve.case.created", payload)
    return case


# ------------------------------------------------------------------ destinations


def _is_current(session: Session, tenant_id: uuid.UUID, destination_id: uuid.UUID) -> bool:
    stmt = scoped_select(ContactDestination, tenant_id).where(
        ContactDestination.supersedes_id == destination_id
    )
    return session.execute(stmt).first() is None


def destination_facts(
    session: Session, tenant_id: uuid.UUID, dest: ContactDestination
) -> DestinationFacts:
    try:
        normalize_e164(dest.phone)
        valid = dest.phone.startswith("+")
    except InvalidPhoneNumberError:
        valid = False
    return DestinationFacts(
        case_id=dest.case_id,
        provenance=dest.provenance,
        verification_status=dest.verification_status,
        has_evidence=bool(dest.source_url_or_evidence.get("reference")),
        is_current=_is_current(session, tenant_id, dest.id),
        valid_e164=valid,
    )


def list_destinations(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[ContactDestination]:
    stmt = (
        scoped_select(ContactDestination, tenant_id)
        .where(ContactDestination.case_id == case_id)
        .order_by(ContactDestination.created_at, ContactDestination.id)
    )
    return list(session.execute(stmt).scalars())


def add_destination(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    *,
    phone: str,
    provenance: DestinationProvenance,
    source: dict[str, Any],
) -> ContactDestination:
    """Register a proposed endpoint. It can NEVER be created as verified here (R-002)."""
    if provenance is DestinationProvenance.INDEPENDENTLY_VERIFIED:
        raise UnprocessableError(
            "use the verify endpoint with evidence to mark a destination verified",
            code="USE_VERIFY_ENDPOINT",
        )
    try:
        e164 = normalize_e164(phone)
    except InvalidPhoneNumberError as exc:
        raise UnprocessableError("invalid phone number", code="INVALID_PHONE") from exc
    dest = ContactDestination(
        tenant_id=tenant_id,
        case_id=case.id,
        phone=e164,
        provenance=provenance.value,
        source_url_or_evidence=source,
        verification_status=VerificationStatus.UNVERIFIED.value,
    )
    session.add(dest)
    session.flush()
    actor = actor_for_user(user_id)
    state = machine.current_state(case)
    if state in {
        R.CREATED,
        R.NEXT_STEP_REVIEW,
        R.REOPENED,
        R.ACKNOWLEDGED,
        R.UNCONFIRMED,
        R.REFUSED,
        R.INTERRUPTED,
        R.MONITORING,
    }:
        machine.advance_to(session, case, R.DESTINATION_REVIEW, actor=actor, context={})
    elif state not in _PRE_CONTACT_STATES:
        raise ConflictError(f"cannot add a destination while case is {state}", code="INVALID_STATE")
    payload = {
        "case_id": str(case.id),
        "destination_id": str(dest.id),
        "provenance": dest.provenance,
        "phone": mask_phone(e164),
    }
    add_timeline(session, tenant_id, case.id, "destination.proposed", actor, payload)
    emit(session, tenant_id, Topic.DESTINATION_PROPOSED, payload, ("destination.proposed", dest.id))
    return dest


def verify_destination(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    dest: ContactDestination,
    *,
    evidence_kind: str,
    reference: str,
    note: str = "",
    confirms_entity: bool = False,
) -> ContactDestination:
    """Append a NEW verified row superseding `dest` (evidence is never edited in place)."""
    if evidence_kind not in _VERIFIABLE_KINDS or not reference.strip():
        raise UnprocessableError(
            "independent verification needs an evidence kind and a reference",
            code="EVIDENCE_REQUIRED",
        )
    if dest.provenance == DestinationProvenance.INDEPENDENTLY_VERIFIED.value and _is_current(
        session, tenant_id, dest.id
    ):
        raise ConflictError("destination already verified", code="ALREADY_VERIFIED")
    if not _is_current(session, tenant_id, dest.id):
        raise ConflictError("destination was superseded", code="DESTINATION_SUPERSEDED")
    actor = actor_for_user(user_id)
    verified = ContactDestination(
        tenant_id=tenant_id,
        case_id=case.id,
        phone=dest.phone,
        provenance=DestinationProvenance.INDEPENDENTLY_VERIFIED.value,
        source_url_or_evidence={
            "kind": evidence_kind,
            "reference": reference.strip(),
            "note": note,
            "confirms_entity": confirms_entity,
            "label": "USER_ATTESTED",
        },
        verification_status=VerificationStatus.VERIFIED.value,
        verified_at=utcnow(),
        verified_by_user_id=user_id,
        supersedes_id=dest.id,
    )
    session.add(verified)
    session.flush()
    _demote_after_destination_change(session, tenant_id, case, dest.id, actor)
    ctx = {"destination_independently_verified": True}
    state = machine.current_state(case)
    if state is not R.VERIFIED_DESTINATION:
        machine.advance_to(session, case, R.VERIFIED_DESTINATION, actor=actor, context=ctx)
    payload = {
        "case_id": str(case.id),
        "destination_id": str(verified.id),
        "supersedes": str(dest.id),
        "evidence_kind": evidence_kind,
        "phone": mask_phone(verified.phone),
    }
    add_timeline(session, tenant_id, case.id, "destination.verified", actor, payload)
    emit(
        session,
        tenant_id,
        Topic.DESTINATION_VERIFIED,
        payload,
        ("destination.verified", verified.id),
    )
    record_audit(session, tenant_id, actor, "resolve.destination.verified", payload)
    return verified


def _demote_after_destination_change(
    session: Session, tenant_id: uuid.UUID, case: ResolutionCase, old_id: uuid.UUID, actor: str
) -> None:
    """Destination changed: expire open authorizations for the old row, drop approval."""
    stmt = scoped_select(ContactAuthorization, tenant_id).where(
        ContactAuthorization.destination_id == old_id,
        ContactAuthorization.consumed_at.is_(None),
        ContactAuthorization.expires_at > utcnow(),
    )
    for auth in session.execute(stmt).scalars():
        auth.expires_at = utcnow()
    if machine.current_state(case) is R.USER_APPROVED_CONTACT:
        machine.transition(
            session,
            case,
            R.VERIFIED_DESTINATION,
            actor=actor,
            note="destination changed; approval withdrawn",
        )


# ------------------------------------------------------------------ corrections


def correct_case_field(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case: ResolutionCase,
    field_name: str,
    new_value: str,
    reason: str,
) -> None:
    """Corrections keep the previous value in the timeline/ledger (append-only history)."""
    if field_name not in _CORRECTABLE:
        raise UnprocessableError(f"field {field_name} cannot be corrected", code="BAD_FIELD")
    old_value = str(getattr(case, field_name))
    setattr(case, field_name, new_value.strip())
    case.updated_at = utcnow()
    actor = actor_for_user(user_id)
    payload = {
        "case_id": str(case.id),
        "field": field_name,
        "old": old_value,
        "new": new_value.strip(),
        "reason": reason,
    }
    add_timeline(session, tenant_id, case.id, "correction.recorded", actor, payload)
    emit(
        session,
        tenant_id,
        Topic.CORRECTION_RECORDED,
        payload,
        ("correction.recorded", case.id, uuid.uuid4().hex[:12]),
    )
    record_audit(session, tenant_id, actor, "resolve.case.corrected", payload)


def alleged_entity_status(session: Session, tenant_id: uuid.UUID, case: ResolutionCase) -> str:
    """The alleged company stays UNVERIFIED until a verified destination's evidence confirms it."""
    for dest in list_destinations(session, tenant_id, case.id):
        if dest.provenance == DestinationProvenance.INDEPENDENTLY_VERIFIED.value and (
            dest.source_url_or_evidence.get("confirms_entity") is True
        ):
            return "SUPPORTED_BY_USER_ATTESTED_EVIDENCE"
    return "UNVERIFIED"


def current_destinations(
    session: Session, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[ContactDestination]:
    superseded = {
        d.supersedes_id for d in list_destinations(session, tenant_id, case_id) if d.supersedes_id
    }
    return [d for d in list_destinations(session, tenant_id, case_id) if d.id not in superseded]


def list_cases(session: Session, tenant_id: uuid.UUID, limit: int = 100) -> list[ResolutionCase]:
    stmt = (
        scoped_select(ResolutionCase, tenant_id)
        .order_by(ResolutionCase.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())
