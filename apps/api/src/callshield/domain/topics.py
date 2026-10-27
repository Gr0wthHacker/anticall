"""Ledger event topics: PRD 8.2 plus extras the modules need."""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    # --- PRD 8.2
    CALL_RECEIVED = "call.received"
    CALL_ANSWERED = "call.answered"
    CALL_MEDIA_STARTED = "call.media.started"
    CALL_TRANSFERRED = "call.transferred"
    CALL_ENDED = "call.ended"
    CASE_CREATED = "case.created"
    DESTINATION_VERIFIED = "destination.verified"
    CONTACT_AUTHORIZED = "contact.authorized"
    CONTACT_STARTED = "contact.started"
    IVR_OPTION_SELECTED = "ivr.option.selected"
    REQUEST_DELIVERED = "request.delivered"
    REQUEST_ACKNOWLEDGED = "request.acknowledged"
    CONTACT_ENDED = "contact.ended"
    CASE_REOPENED = "case.reopened"
    EVIDENCE_CREATED = "evidence.created"
    COMPLAINT_DRAFTED = "complaint.drafted"
    ARTIFACT_EXPIRED = "artifact.expired"
    # --- extras
    LINE_VERIFIED = "line.verified"
    CALL_ROUTED = "call.routed"
    CALL_FAILED = "call.failed"
    ROUTING_FALLBACK = "routing.fallback"
    SCREENING_DISABLED = "screening.disabled"
    CLASSIFICATION_CREATED = "classification.created"
    INCIDENT_CREATED = "incident.created"
    INCIDENT_LINKED = "incident.linked"
    INCIDENT_UNLINKED = "incident.unlinked"
    CASE_STATE_CHANGED = "case.state_changed"
    DESTINATION_PROPOSED = "destination.proposed"
    CONTACT_BLOCKED = "contact.blocked"
    CONTACT_ABORTED = "contact.aborted"
    IVR_MENU_DETECTED = "ivr.menu.detected"
    ATTEMPT_REVIEWED = "attempt.reviewed"
    EVIDENCE_REDACTED = "evidence.redacted"
    EVIDENCE_DELETED = "evidence.deleted"
    CORRECTION_RECORDED = "correction.recorded"
    ASSESSMENT_COMPLETED = "assessment.completed"
    FILING_DRAFTED = "filing.drafted"
    FILING_APPROVED = "filing.approved"
    FILING_SUBMITTED = "filing.submitted"
    FILING_FAILED = "filing.failed"
    FILING_ACKNOWLEDGED = "filing.acknowledged"
    EXPORT_REQUESTED = "export.requested"
    EXPORT_COMPLETED = "export.completed"
    COMMUNITY_OPT_IN = "community.opt_in"
    COMMUNITY_OPT_OUT = "community.opt_out"


CURRENT_SCHEMA_VERSION = 1
