"""Public provider webhook. Authenticated by per-line HMAC signature, NOT by JWT."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from callshield.auth.deps import get_app_settings
from callshield.core.config import Settings
from callshield.core.errors import ApiError
from callshield.db.session import get_session
from callshield.defend.ingest import process_webhook
from callshield.telephony.adapters import MAX_BODY_BYTES

router = APIRouter(tags=["webhooks"])


async def raw_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ApiError("payload too large", code="PAYLOAD_TOO_LARGE", status_code=413)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ApiError("payload too large", code="PAYLOAD_TOO_LARGE", status_code=413)
    return body


@router.post("/v1/webhooks/{provider}/{line_key}")
def provider_webhook(
    provider: str,
    line_key: str,
    request: Request,
    body: Annotated[bytes, Depends(raw_body)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, Any]:
    outcome = process_webhook(session, settings, provider, line_key, dict(request.headers), body)
    session.commit()  # the 200 means the incident row is durable
    return {
        "status": outcome.status,
        "call_id": str(outcome.call_id) if outcome.call_id else None,
        "incident_id": str(outcome.incident_id) if outcome.incident_id else None,
        "instructions": [i.model_dump(exclude_none=True) for i in outcome.instructions],
        "detail": outcome.detail,
    }
