"""Provider-reported outbound attestation for a protected line (feeds callback caller-ID gate).

Authenticated like every provider webhook: per-line HMAC signature, fresh nonce, tenant taken
ONLY from the verified line. Users cannot call this; only the line's provider can. Anything
that is not exactly "A" is stored as reported (B, C) or UNKNOWN and keeps callbacks blocked.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from callshield.api.routers.webhooks import raw_body
from callshield.audit import record_audit
from callshield.auth.deps import get_app_settings
from callshield.callback.lines import record_outbound_attestation
from callshield.core.config import Settings
from callshield.core.errors import ApiError, ForbiddenError
from callshield.db.session import get_session
from callshield.defend.ingest import authenticate_line_webhook
from callshield.domain.enums import LineVerificationStatus

router = APIRouter(tags=["webhooks"])


class OutboundAttestationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = Field(min_length=1, max_length=16)


def _parse_report(body: bytes) -> OutboundAttestationReport:
    try:
        return OutboundAttestationReport.model_validate_json(body)
    except ValidationError as exc:
        raise ApiError("malformed attestation report", code="MALFORMED_EVENT") from exc


@router.post("/v1/webhooks/{provider}/{line_key}/outbound-attestation")
def outbound_attestation_webhook(
    provider: str,
    line_key: str,
    request: Request,
    body: Annotated[bytes, Depends(raw_body)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, Any]:
    line, _endpoint, _adapter = authenticate_line_webhook(
        session, settings, provider, line_key, dict(request.headers), body
    )
    report = _parse_report(body)
    if line.verification_status != LineVerificationStatus.VERIFIED.value:
        raise ForbiddenError("line is not verified", code="LINE_NOT_VERIFIED")
    row = record_outbound_attestation(
        session, line.tenant_id, line.id, report.level, f"provider:{provider}"
    )
    record_audit(
        session,
        line.tenant_id,
        f"provider:{provider}",
        "callback.outbound_attestation_reported",
        {"line_id": str(line.id), "level": row.level},
    )
    session.commit()
    return {"status": "recorded", "level": row.level}
