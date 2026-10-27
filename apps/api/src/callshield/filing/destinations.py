"""Destination registry (PRD 19.8): seed data, idempotent seeding and lookups.

Honesty rule: every shipped destination is `manual_handoff`. No documented, authorized
third-party submission API has been identified for any of them (reviewed 2026-10-27).
A destination only becomes `documented_api` through an explicit registry entry plus a
registered adapter (see filing.adapters), never by default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.core.ids import new_id
from callshield.db.models.enforce import DestinationRegistry
from callshield.db.models.filing_ext import DestinationRegistryExt
from callshield.domain.enums import AdapterMethod, Agency

SEED_VERSION = "2026-10-27.1"
LAST_REVIEWED = datetime(2026, 10, 27, tzinfo=UTC)
_NO_API = (
    "Reviewed 2026-10-27 from the official public pages cited in source_url. No documented, "
    "authorized third-party submission API was identified, so this destination is treated "
    "as manual handoff. This is a documentation review, not a live probe."
)
_DEFAULT_RETENTION = (
    "Retention by the receiving body is set by that body and is not controlled by CallShield. "
    "CallShield keeps the draft, approval and any receipt under the tenant case-retention policy."
)
_DEFAULT_SUNSET = (
    "If the destination changes or retires its form or channel, set active=false: new drafts are "
    "blocked with UNSUPPORTED_DESTINATION, existing drafts and filing history are kept unchanged."
)
_RECEIPT_NOTICE = (
    "A confirmation or receipt shows the report was received. It is not a finding, an "
    "adjudication, or a guarantee of investigation, compensation, or that calls will stop."
)


@dataclass(frozen=True)
class DestinationSeed:
    slug: str
    agency: str
    name: str
    source_url: str
    template_key: str | None
    validated: bool
    supported_issue_types: tuple[str, ...]
    supported_fields: tuple[str, ...]
    authorization_requirements: str
    manual_handoff: dict[str, Any]
    notices: tuple[str, ...] = field(default_factory=tuple)
    method: str = AdapterMethod.MANUAL_HANDOFF.value
    capability_evidence: str = _NO_API
    attachment_constraints: dict[str, Any] = field(
        default_factory=lambda: {
            "attachments_accepted_via_handoff": False,
            "allowed_media_types": [],
            "audio_allowed": False,
            "note": "Attachments are not transmitted by CallShield; keep originals yourself.",
        }
    )
    retention: str = _DEFAULT_RETENTION
    sunset_procedure: str = _DEFAULT_SUNSET


_COMMON_FIELDS = ("complainant_line", "calls", "caller_number", "narrative")
_USER_AUTH = (
    "The account holder must verify the protected line, review the exact text, and approve "
    "each filing with the filing attestation. CallShield never submits on this destination."
)

DESTINATION_SEEDS: tuple[DestinationSeed, ...] = (
    DestinationSeed(
        slug="ftc-donotcall",
        agency=Agency.FTC.value,
        name="FTC DoNotCall.gov: report unwanted calls",
        source_url="https://www.donotcall.gov/report.html",
        template_key="ftc_dnc_v1",
        validated=True,
        supported_issue_types=(
            "DNC_REGISTRY",
            "DNC_ENTITY_SPECIFIC",
            "AUTOMATED_OR_PRERECORDED_CALL",
        ),
        supported_fields=(*_COMMON_FIELDS, "contact_kind", "dnc_registration_status"),
        authorization_requirements=_USER_AUTH,
        manual_handoff={
            "official_url": "https://www.donotcall.gov/report.html",
            "steps": [
                "Open the official FTC DoNotCall.gov report page yourself.",
                "Copy the prepared text and fields below into the form.",
                "Complete any verification the site asks for; do not automate it.",
                "Come back and record that you filed, adding the confirmation number only "
                "if the site gave you one.",
            ],
        },
        notices=(_RECEIPT_NOTICE,),
    ),
    DestinationSeed(
        slug="ftc-reportfraud",
        agency=Agency.FTC.value,
        name="FTC ReportFraud.ftc.gov: report scams and fraud",
        source_url="https://reportfraud.ftc.gov/",
        template_key="ftc_fraud_v1",
        validated=True,
        supported_issue_types=("CALLER_ID_SPOOFING", "OTHER"),
        supported_fields=(*_COMMON_FIELDS, "payment_made"),
        authorization_requirements=_USER_AUTH,
        manual_handoff={
            "official_url": "https://reportfraud.ftc.gov/",
            "steps": [
                "Open the official ReportFraud.ftc.gov page yourself.",
                "Copy the prepared text and fields below into the form.",
                "Record that you filed, with the report number only if one was shown to you.",
            ],
        },
        notices=(_RECEIPT_NOTICE,),
    ),
    DestinationSeed(
        slug="fcc-unwanted-call",
        agency=Agency.FCC.value,
        name="FCC Consumer Complaint Center: unwanted calls",
        source_url="https://consumercomplaints.fcc.gov/",
        template_key="fcc_unwanted_call_v1",
        validated=True,
        supported_issue_types=("AUTOMATED_OR_PRERECORDED_CALL", "CALLER_ID_SPOOFING", "OTHER"),
        supported_fields=(*_COMMON_FIELDS, "contact_kind"),
        authorization_requirements=_USER_AUTH,
        capability_evidence=(
            _NO_API
            + " FCC process: https://consumercomplaints.fcc.gov/hc/en-us/articles/202752940-"
            "How-the-FCC-Handles-Your-Complaint"
        ),
        manual_handoff={
            "official_url": "https://consumercomplaints.fcc.gov/",
            "steps": [
                "Open the official FCC Consumer Complaint Center yourself.",
                "Choose the unwanted-calls complaint and copy the prepared fields into it.",
                "Record that you filed, with the ticket number only if the FCC showed you one.",
            ],
        },
        notices=(
            "The FCC does not resolve individual unwanted-call complaints. It uses complaints "
            "for enforcement and policy work.",
            "An FCC receipt or ticket number is not adjudication. Filing an FCC complaint does "
            "not start a private lawsuit.",
            _RECEIPT_NOTICE,
        ),
    ),
    DestinationSeed(
        slug="carrier-generic",
        agency=Agency.CARRIER.value,
        name="Your phone carrier: spam and fraud report (generic)",
        source_url="https://www.fcc.gov/consumers/guides/stop-unwanted-robocalls-and-texts",
        template_key="carrier_report_v1",
        validated=True,
        supported_issue_types=("AUTOMATED_OR_PRERECORDED_CALL", "CALLER_ID_SPOOFING", "OTHER"),
        supported_fields=(*_COMMON_FIELDS, "carrier_name"),
        authorization_requirements=_USER_AUTH,
        manual_handoff={
            "official_url": "https://www.fcc.gov/consumers/guides/stop-unwanted-robocalls-and-texts",
            "steps": [
                "Use your own carrier's official reporting channel (its app, account portal "
                "or support); CallShield cannot know which channel yours offers.",
                "Copy the prepared text into that channel.",
                "Record that you reported it, with a reference number only if one was given.",
            ],
        },
        notices=("Channels differ by carrier. This entry is generic and not carrier-specific.",),
    ),
    DestinationSeed(
        slug="state-ag-generic",
        agency=Agency.STATE_AG.value,
        name="State attorney general complaint (NOT VALIDATED)",
        source_url="https://www.naag.org/find-my-ag/",
        template_key=None,
        validated=False,
        supported_issue_types=(),
        supported_fields=(),
        authorization_requirements="Not validated. No template, fields or channel reviewed.",
        capability_evidence=(
            "NOT VALIDATED. State forms, required fields and consent rules vary and have not "
            "been reviewed. No electronic submission path is supported."
        ),
        manual_handoff={
            "official_url": "https://www.naag.org/find-my-ag/",
            "steps": [
                "Find your state attorney general's consumer-complaint page yourself.",
                "CallShield does not generate a draft for state attorneys general yet.",
            ],
        },
        notices=("NOT VALIDATED: drafts are not generated for this destination.",),
    ),
)


@dataclass(frozen=True)
class DestinationView:
    id: uuid.UUID
    slug: str
    agency: str
    name: str
    method: str
    source_url: str
    capability_evidence: str
    last_reviewed_at: datetime | None
    authorization_requirements: str
    supported_issue_types: list[Any]
    supported_fields: list[Any]
    attachment_constraints: dict[str, Any]
    retention: str
    sunset_procedure: str
    active: bool
    template_key: str | None
    validated: bool
    manual_handoff: dict[str, Any]
    notices: list[Any]


def _apply_seed(
    row: DestinationRegistry, ext: DestinationRegistryExt, seed: DestinationSeed
) -> None:
    row.agency = seed.agency
    row.name = seed.name
    row.method = seed.method
    row.source_url = seed.source_url
    row.capability_evidence = seed.capability_evidence
    row.last_reviewed_at = LAST_REVIEWED
    row.authorization_requirements = seed.authorization_requirements
    row.supported_issue_types = list(seed.supported_issue_types)
    row.supported_fields = list(seed.supported_fields)
    row.attachment_constraints = dict(seed.attachment_constraints)
    row.retention = seed.retention
    row.sunset_procedure = seed.sunset_procedure
    ext.template_key = seed.template_key
    ext.validated = seed.validated
    ext.manual_handoff = dict(seed.manual_handoff)
    ext.notices = list(seed.notices)
    ext.seed_version = SEED_VERSION


def upsert_destination(session: Session, seed: DestinationSeed, active: bool = True) -> None:
    """Create or refresh one registry entry (idempotent; same seed_version => no-op)."""
    ext = session.execute(
        select(DestinationRegistryExt).where(DestinationRegistryExt.slug == seed.slug)
    ).scalar_one_or_none()
    if ext is not None:
        if ext.seed_version == SEED_VERSION:
            return
        row = session.get(DestinationRegistry, ext.destination_id)
        if row is not None:
            _apply_seed(row, ext, seed)
        return
    row = DestinationRegistry(id=new_id(), active=active)
    ext = DestinationRegistryExt(id=new_id(), destination_id=row.id, slug=seed.slug)
    _apply_seed(row, ext, seed)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
            session.add(ext)
            session.flush()
    except IntegrityError:
        return  # lost a race with another seeder; the winner's rows are equivalent


def ensure_registry_seeded(session: Session) -> None:
    """Idempotent. Services call this lazily; there is no startup hook and no endpoint."""
    existing = {
        slug: version
        for slug, version in session.execute(
            select(DestinationRegistryExt.slug, DestinationRegistryExt.seed_version)
        ).all()
    }
    for seed in DESTINATION_SEEDS:
        if existing.get(seed.slug) != SEED_VERSION:
            upsert_destination(session, seed)
    session.flush()


def _view(row: DestinationRegistry, ext: DestinationRegistryExt) -> DestinationView:
    return DestinationView(
        id=row.id,
        slug=ext.slug,
        agency=row.agency,
        name=row.name,
        method=row.method,
        source_url=row.source_url,
        capability_evidence=row.capability_evidence,
        last_reviewed_at=row.last_reviewed_at,
        authorization_requirements=row.authorization_requirements,
        supported_issue_types=list(row.supported_issue_types or []),
        supported_fields=list(row.supported_fields or []),
        attachment_constraints=dict(row.attachment_constraints or {}),
        retention=row.retention,
        sunset_procedure=row.sunset_procedure,
        active=row.active,
        template_key=ext.template_key,
        validated=ext.validated,
        manual_handoff=dict(ext.manual_handoff or {}),
        notices=list(ext.notices or []),
    )


def list_destinations(session: Session) -> list[DestinationView]:
    ensure_registry_seeded(session)
    rows = session.execute(
        select(DestinationRegistry, DestinationRegistryExt)
        .join(
            DestinationRegistryExt, DestinationRegistryExt.destination_id == DestinationRegistry.id
        )
        .order_by(DestinationRegistryExt.slug)
    ).all()
    return [_view(r, e) for r, e in rows]


def get_destination(session: Session, slug: str) -> DestinationView | None:
    ensure_registry_seeded(session)
    pair = session.execute(
        select(DestinationRegistry, DestinationRegistryExt)
        .join(
            DestinationRegistryExt, DestinationRegistryExt.destination_id == DestinationRegistry.id
        )
        .where(DestinationRegistryExt.slug == slug)
    ).first()
    return _view(*pair) if pair else None


def get_destination_by_id(session: Session, destination_id: uuid.UUID) -> DestinationView | None:
    pair = session.execute(
        select(DestinationRegistry, DestinationRegistryExt)
        .join(
            DestinationRegistryExt, DestinationRegistryExt.destination_id == DestinationRegistry.id
        )
        .where(DestinationRegistry.id == destination_id)
    ).first()
    return _view(*pair) if pair else None
