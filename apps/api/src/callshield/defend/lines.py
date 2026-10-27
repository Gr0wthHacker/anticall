"""Protected line service: enrollment, config, screening toggle (D-001, D-010)."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from callshield.audit import record_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, PolicyBlockedError, UnprocessableError
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.defend_ext import LineEndpoint
from callshield.defend.consent import current_decision
from callshield.defend.schemas import LineOut, WebhookCredentials
from callshield.domain.enums import EventOrigin, LineVerificationStatus
from callshield.domain.topics import Topic
from callshield.ledger import append_event
from callshield.policy.config import LineRoutingConfig, parse_routing_config
from callshield.telephony.registry import adapter_names


def is_routable(line: ProtectedLine) -> bool:
    return line.verification_status == LineVerificationStatus.VERIFIED.value


def line_out(line: ProtectedLine) -> LineOut:
    return LineOut(
        id=line.id,
        e164=line.e164,
        label=line.label,
        verification_status=line.verification_status,
        verified_at=line.verified_at,
        screening_enabled=line.screening_enabled,
        routing_config=line.routing_config or {},
        routable=is_routable(line),
        created_at=line.created_at,
    )


def _validated_config(e164: str, data: dict[str, Any]) -> LineRoutingConfig:
    try:
        config = LineRoutingConfig(**data)
    except ValidationError as exc:
        raise UnprocessableError(
            "invalid routing_config",
            code="INVALID_ROUTING_CONFIG",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc
    if config.user_number == e164:
        raise UnprocessableError(
            "user_number must differ from the protected line (call loop)", code="ROUTING_LOOP"
        )
    return config


def create_line(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    e164: str,
    label: str,
    provider: str,
    endpoint_id: str | None,
    config_in: LineRoutingConfig,
) -> tuple[ProtectedLine, WebhookCredentials]:
    if provider not in adapter_names():
        raise UnprocessableError("unsupported provider", code="UNSUPPORTED_PROVIDER")
    exists = session.execute(
        select(ProtectedLine.id).where(
            ProtectedLine.tenant_id == tenant_id, ProtectedLine.e164 == e164
        )
    ).first()
    if exists is not None:
        raise ConflictError("line already enrolled", code="LINE_EXISTS")
    config = _validated_config(e164, config_in.model_dump())
    line = ProtectedLine(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        e164=e164,
        label=label,
        routing_config=config.model_dump(),
    )
    session.add(line)
    session.flush()
    creds = _new_endpoint(session, line, provider, endpoint_id)
    record_audit(
        session,
        tenant_id,
        f"user:{user_id}",
        "line.enrolled",
        {"line_id": str(line.id), "provider": provider},
    )
    return line, creds


def _new_endpoint(
    session: Session, line: ProtectedLine, provider: str, endpoint_id: str | None
) -> WebhookCredentials:
    line_key = secrets.token_urlsafe(24)
    secret = secrets.token_urlsafe(32)
    session.add(
        LineEndpoint(
            tenant_id=line.tenant_id,
            line_id=line.id,
            provider=provider,
            line_key=line_key,
            endpoint_id=endpoint_id,
            webhook_secret=secret,
        )
    )
    return WebhookCredentials(
        url_path=f"/v1/webhooks/{provider}/{line_key}", line_key=line_key, secret=secret
    )


def rotate_secret(session: Session, line: ProtectedLine, actor: str) -> WebhookCredentials:
    endpoint = session.execute(
        select(LineEndpoint).where(
            LineEndpoint.tenant_id == line.tenant_id, LineEndpoint.line_id == line.id
        )
    ).scalar_one()
    endpoint.webhook_secret = secrets.token_urlsafe(32)
    endpoint.rotated_at = utcnow()
    record_audit(session, line.tenant_id, actor, "line.secret_rotated", {"line_id": str(line.id)})
    return WebhookCredentials(
        url_path=f"/v1/webhooks/{endpoint.provider}/{endpoint.line_key}",
        line_key=endpoint.line_key,
        secret=endpoint.webhook_secret,
    )


def update_line(
    session: Session,
    line: ProtectedLine,
    actor: str,
    label: str | None,
    config_patch: dict[str, Any] | None,
) -> ProtectedLine:
    if label is not None:
        line.label = label
    if config_patch is not None:
        merged = {**(line.routing_config or {}), **config_patch}
        line.routing_config = _validated_config(line.e164, merged).model_dump()
    record_audit(
        session,
        line.tenant_id,
        actor,
        "line.updated",
        {"line_id": str(line.id), "config_keys": sorted((config_patch or {}).keys())},
    )
    return line


def set_screening(
    session: Session, settings: Settings, line: ProtectedLine, enabled: bool, actor: str
) -> dict[str, Any]:
    """One-click toggle. Disable is immediate: routing reads the flag on every call/turn."""
    if enabled and not is_routable(line):
        raise PolicyBlockedError("line is not verified", code="LINE_NOT_VERIFIED")
    changed = line.screening_enabled != enabled
    line.screening_enabled = enabled
    record_audit(
        session,
        line.tenant_id,
        actor,
        "screening.enabled" if enabled else "screening.disabled",
        {"line_id": str(line.id)},
    )
    if changed and not enabled:
        append_event(
            session,
            line.tenant_id,
            Topic.SCREENING_DISABLED,
            {"line_id": str(line.id), "screening_enabled": enabled, "actor": actor},
            EventOrigin.USER_ACTION,
            f"screening-toggle:{line.id}:{uuid.uuid4()}",
        )
    decision = current_decision(session, settings, line)
    config = parse_routing_config(line.routing_config)
    effective = (
        "SCREEN"
        if enabled and decision.allow_live_stt
        else f"ORDINARY:{config.unscreened_unknown_action}"
    )
    return {"effective_routing": effective, "processing_allows_screening": decision.allow_live_stt}


def get_line(session: Session, tenant_id: uuid.UUID, line_id: uuid.UUID) -> ProtectedLine:
    return scoped_get_or_404(session, ProtectedLine, line_id, tenant_id)
