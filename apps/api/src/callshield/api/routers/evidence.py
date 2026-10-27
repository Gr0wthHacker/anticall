"""Evidence endpoints (PRD 19.7, ENF-005/010/020). Storage keys are never exposed."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from callshield.auth.deps import TenantContext, get_app_settings, tenant_context
from callshield.auth.scoping import scoped_get_or_404
from callshield.core.config import Settings
from callshield.core.errors import ForbiddenError, NotFoundError, UnprocessableError
from callshield.db.models.enforce import Case
from callshield.domain.enums import (
    AuthorizationState,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
    Role,
)
from callshield.evidence.access import (
    describe_artifact,
    get_artifact,
    require_content_access,
    set_authorization,
)
from callshield.evidence.derive import (
    RedactionSpec,
    redact_artifact,
    revise_transcript,
    transcript_versions,
)
from callshield.evidence.ingest import ArtifactInput, ingest_artifact
from callshield.evidence.integrity import verify_artifact
from callshield.evidence.retention import set_legal_hold
from callshield.evidence.rights import (
    build_data_export,
    request_artifact_deletion,
    withdraw_processing,
)
from callshield.evidence.store import ArtifactStore, get_artifact_store
from callshield.evidence.taxonomy import DEFAULT_LABEL

router = APIRouter(tags=["enforce-evidence"])

Ctx = Annotated[TenantContext, Depends(tenant_context)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
MAX_B64_CHARS = 35_000_000
HOLD_ROLES = frozenset({Role.REVIEWER, Role.OPERATOR, Role.HOUSEHOLD_ADMIN})
ELEVATED_ROLES = frozenset({Role.REVIEWER, Role.OPERATOR})
SYSTEM_ONLY_TYPES = frozenset(
    {
        EvidenceType.SIGNALING_METADATA,
        EvidenceType.OBSERVED_CLI,
        EvidenceType.STIR_SHAKEN,
        EvidenceType.CORRELATED_CALL,
    }
)
UPLOAD_ORIGINS = frozenset(
    {
        EvidenceOrigin.ORIGINAL,
        EvidenceOrigin.USER_STATED,
        EvidenceOrigin.EXTERNAL_DOCUMENTARY,
        EvidenceOrigin.CALLER_CLAIMED,
    }
)


def store_dependency() -> ArtifactStore:
    return get_artifact_store()


Store = Annotated[ArtifactStore, Depends(store_dependency)]


class UploadRequest(BaseModel):
    source_type: EvidenceType
    content_base64: str = Field(max_length=MAX_B64_CHARS)
    media_type: str = Field(default="application/octet-stream", max_length=100)
    origin: EvidenceOrigin = EvidenceOrigin.USER_STATED
    evidence_label: EvidenceLabel | None = None
    collection_method: str = Field(default="user_upload", max_length=100)
    collected_at: datetime | None = None
    processing_authorization: AuthorizationState
    sharing_authorization: AuthorizationState
    provenance: dict[str, Any] = Field(default_factory=dict)
    incident_id: uuid.UUID | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class RedactRequest(BaseModel):
    terms: list[str] = Field(default_factory=list, max_length=200)
    redact_phone_numbers: bool = True
    replacement_content_base64: str | None = Field(default=None, max_length=MAX_B64_CHARS)


class RevisionRequest(BaseModel):
    corrected_text: str = Field(min_length=1, max_length=2_000_000)
    reason: str = Field(min_length=1, max_length=1000)


class ReasonRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class HoldRequest(BaseModel):
    hold: bool
    reason: str = Field(min_length=1, max_length=1000)


class AuthorizationRequest(BaseModel):
    field: str = Field(pattern="^(processing_authorization|sharing_authorization)$")
    state: AuthorizationState


def _decode(content: str) -> bytes:
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnprocessableError("content is not valid base64", code="BAD_CONTENT") from exc


def _upload_label(body: UploadRequest, role: Role) -> EvidenceLabel:
    """User uploads are USER_ATTESTED; only reviewers may mark EXTERNALLY_VERIFIED."""
    if body.evidence_label is None:
        return (
            EvidenceLabel.USER_ATTESTED
            if body.origin in (EvidenceOrigin.ORIGINAL, EvidenceOrigin.EXTERNAL_DOCUMENTARY)
            else DEFAULT_LABEL[body.origin]
        )
    if body.evidence_label is EvidenceLabel.EXTERNALLY_VERIFIED and role not in ELEVATED_ROLES:
        raise ForbiddenError("only reviewers may mark evidence verified", code="INSUFFICIENT_ROLE")
    if body.evidence_label is EvidenceLabel.OBSERVED:
        raise UnprocessableError("uploads cannot be labeled OBSERVED", code="TAXONOMY_MISMATCH")
    return body.evidence_label


@router.post("/v1/cases/{case_id}/evidence", status_code=201)
def upload_evidence(
    case_id: uuid.UUID, body: UploadRequest, ctx: Ctx, store: Store, settings: AppSettings
) -> dict[str, Any]:
    scoped_get_or_404(ctx.session, Case, case_id, ctx.tenant_id)
    if body.source_type in SYSTEM_ONLY_TYPES or body.origin not in UPLOAD_ORIGINS:
        raise UnprocessableError("this evidence type is system-generated", code="NOT_UPLOADABLE")
    artifact = ingest_artifact(
        ctx.session, store, settings, ctx.tenant_id,
        ArtifactInput(
            data=_decode(body.content_base64),
            source_type=body.source_type,
            origin=body.origin,
            evidence_label=_upload_label(body, ctx.principal.role),
            media_type=body.media_type,
            collection_method=body.collection_method,
            collected_at=body.collected_at,
            processing_authorization=body.processing_authorization,
            sharing_authorization=body.sharing_authorization,
            provenance={**body.provenance, "uploaded_by": ctx.principal.actor},
            case_id=case_id,
            incident_id=body.incident_id,
            retention_days=body.retention_days,
        ),
        ctx.principal.actor,
    )  # fmt: skip
    return describe_artifact(artifact)


@router.get("/v1/evidence/{artifact_id}")
def get_evidence(
    artifact_id: uuid.UUID, ctx: Ctx, store: Store, verify: Annotated[bool, Query()] = False
) -> dict[str, Any]:
    artifact = get_artifact(ctx.session, ctx.tenant_id, artifact_id)
    view = describe_artifact(artifact)
    if verify:
        view["integrity"] = verify_artifact(store, artifact).as_dict()
    return view


@router.get("/v1/evidence/{artifact_id}/content")
def get_evidence_content(artifact_id: uuid.UUID, ctx: Ctx, store: Store) -> Response:
    """Owner download of stored content; denied when deleted or processing is not authorized."""
    artifact = get_artifact(ctx.session, ctx.tenant_id, artifact_id)
    require_content_access(artifact)
    assert artifact.storage_key is not None
    return Response(
        content=store.get(artifact.storage_key),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.id}"',
            "X-Original-Media-Type": artifact.media_type,
            "X-Content-SHA256": artifact.sha256 or "",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/v1/evidence/{artifact_id}/versions")
def get_versions(artifact_id: uuid.UUID, ctx: Ctx) -> dict[str, Any]:
    chain = transcript_versions(ctx.session, ctx.tenant_id, artifact_id)
    return {"versions": [describe_artifact(a) for a in chain]}


@router.post("/v1/cases/{case_id}/evidence/{artifact_id}/redact", status_code=201)
def redact(
    case_id: uuid.UUID, artifact_id: uuid.UUID, body: RedactRequest, ctx: Ctx, store: Store,
    settings: AppSettings,
) -> dict[str, Any]:  # fmt: skip
    """Create a redacted derivative. The original stays exactly as it was."""
    original = get_artifact(ctx.session, ctx.tenant_id, artifact_id)
    if original.case_id != case_id:
        raise NotFoundError("EvidenceArtifact not found")
    replacement = (
        _decode(body.replacement_content_base64) if body.replacement_content_base64 else None
    )
    spec = RedactionSpec(body.terms, body.redact_phone_numbers, replacement)
    derived = redact_artifact(
        ctx.session, store, settings, ctx.tenant_id, artifact_id, spec, ctx.principal.actor
    )
    return {"derived": describe_artifact(derived), "original": describe_artifact(original)}


@router.post("/v1/evidence/{artifact_id}/transcript-revisions", status_code=201)
def revise(
    artifact_id: uuid.UUID, body: RevisionRequest, ctx: Ctx, store: Store, settings: AppSettings
) -> dict[str, Any]:
    revision = revise_transcript(
        ctx.session, store, settings, ctx.tenant_id, artifact_id, body.corrected_text,
        body.reason, ctx.principal.actor,
    )  # fmt: skip
    return describe_artifact(revision)


@router.post("/v1/evidence/{artifact_id}/deletion-requests")
def deletion_request(
    artifact_id: uuid.UUID, body: ReasonRequest, ctx: Ctx, store: Store
) -> dict[str, Any]:
    """Delete the artifact and derivatives unless a legal hold applies; limits are disclosed."""
    request = request_artifact_deletion(
        ctx.session, store, ctx.tenant_id, ctx.principal.user_id, artifact_id, body.reason
    )
    return {"request_id": str(request.id), "status": request.status, **request.result}


@router.post("/v1/evidence/{artifact_id}/legal-hold")
def legal_hold(artifact_id: uuid.UUID, body: HoldRequest, ctx: Ctx) -> dict[str, Any]:
    if ctx.principal.role not in HOLD_ROLES:
        raise ForbiddenError("insufficient role", code="INSUFFICIENT_ROLE")
    family = set_legal_hold(
        ctx.session, ctx.tenant_id, artifact_id, body.hold, ctx.principal.actor, body.reason
    )
    return {"legal_hold": body.hold, "artifact_ids": [str(a.id) for a in family]}


@router.post("/v1/evidence/{artifact_id}/authorization")
def change_authorization(
    artifact_id: uuid.UUID, body: AuthorizationRequest, ctx: Ctx
) -> dict[str, Any]:
    artifact = set_authorization(
        ctx.session, ctx.tenant_id, artifact_id,
        field=body.field, state=body.state, actor=ctx.principal.actor,
    )  # fmt: skip
    return describe_artifact(artifact)


@router.get("/v1/me/data-export")
def data_export(ctx: Ctx) -> dict[str, Any]:
    """Data-subject export of this account's records (content via the evidence endpoints)."""
    return build_data_export(ctx.session, ctx.tenant_id, ctx.principal.user_id)


@router.post("/v1/me/withdrawal")
def withdrawal(body: ReasonRequest, ctx: Ctx) -> dict[str, Any]:
    """Stop further processing and revoke sharing; deletion is a separate request."""
    request = withdraw_processing(ctx.session, ctx.tenant_id, ctx.principal.user_id, body.reason)
    return {"request_id": str(request.id), "status": request.status, **request.result}
