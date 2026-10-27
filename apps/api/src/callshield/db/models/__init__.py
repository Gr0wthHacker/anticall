"""Import every model so Base.metadata is complete."""

from callshield.db.models import (  # noqa: F401  (register extension tables)
    callback_ext,
    defend_ext,
    enforce_ext,
    filing_ext,
    resolve_ext,
)
from callshield.db.models.community import CampaignLink, CandidateCampaign
from callshield.db.models.defend import (
    CallerObservation,
    CallSession,
    Classification,
    ConsentRecord,
    Incident,
    OrganizationClaim,
    ProtectedLine,
    RoutingRule,
    TrustedContact,
)
from callshield.db.models.enforce import (
    Case,
    CaseAssessment,
    CaseAuditEvent,
    CaseIncident,
    ComplaintDraft,
    DestinationRegistry,
    EvidenceArtifact,
    FilingDraft,
    FilingSubmission,
    SubmissionApproval,
)
from callshield.db.models.platform import AuditEvent, Job, LedgerEvent
from callshield.db.models.resolve import (
    ContactAttempt,
    ContactAuthorization,
    ContactDestination,
    IvrPath,
    OptOutEvent,
    ResolutionCase,
)
from callshield.db.models.tenancy import Tenant, User

__all__ = [
    "AuditEvent",
    "CallSession",
    "CallerObservation",
    "CampaignLink",
    "CandidateCampaign",
    "Case",
    "CaseAssessment",
    "CaseAuditEvent",
    "CaseIncident",
    "Classification",
    "ComplaintDraft",
    "ConsentRecord",
    "ContactAttempt",
    "ContactAuthorization",
    "ContactDestination",
    "DestinationRegistry",
    "EvidenceArtifact",
    "FilingDraft",
    "FilingSubmission",
    "Incident",
    "IvrPath",
    "Job",
    "LedgerEvent",
    "OptOutEvent",
    "OrganizationClaim",
    "ProtectedLine",
    "ResolutionCase",
    "RoutingRule",
    "SubmissionApproval",
    "Tenant",
    "TrustedContact",
    "User",
]
