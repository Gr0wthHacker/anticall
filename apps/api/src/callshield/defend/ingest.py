"""Provider webhook pipeline: resolve line -> verify -> replay check -> parse -> dispatch.

Tenant identity comes ONLY from the line endpoint that matched the URL's (provider,
line_key) and whose secret verified the signature. Payload fields can only be compared to
that line (to-number / endpoint id); a mismatch is rejected, never re-routed.

Delivery contract: each delivery attempt is signed with a fresh nonce and timestamp. A
repeated nonce is a replay and is rejected (409). A fresh attempt carrying an already
processed event is accepted idempotently (status "duplicate"), creating nothing new.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from callshield.core.config import Settings
from callshield.core.errors import ApiError, ForbiddenError
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.defend_ext import LineEndpoint, WebhookReceipt
from callshield.defend.health import handle_registration, touch_health
from callshield.defend.lifecycle import IngestOutcome, handle_call_received
from callshield.defend.screening_flow import handle_screening_failure, handle_utterance
from callshield.defend.transitions import apply_transition
from callshield.domain.enums import CallDirection, LineVerificationStatus
from callshield.llm.client import LLMClient
from callshield.llm.config import build_llm_client
from callshield.telephony.base import (
    MalformedEventError,
    ProviderAdapter,
    ProviderCallEvent,
    ProviderEventType,
    WebhookVerificationError,
)
from callshield.telephony.registry import get_adapter

logger = logging.getLogger("callshield.defend")

_llm_override: list[LLMClient | None] = []
_TRANSITIONS = frozenset(
    {
        ProviderEventType.CALL_ANSWERED,
        ProviderEventType.CALL_TRANSFERRED,
        ProviderEventType.CALL_ENDED,
    }
)


def set_llm_client(client: LLMClient | None) -> None:
    """Install (or with None, clear) an LLM client override. Default is off."""
    _llm_override.clear()
    _llm_override.append(client)


def current_llm() -> LLMClient | None:
    return _llm_override[0] if _llm_override else build_llm_client()


def _rejected() -> ApiError:
    return ApiError("webhook rejected", code="WEBHOOK_REJECTED", status_code=401)


def resolve_endpoint(session: Session, provider: str, line_key: str) -> LineEndpoint | None:
    stmt = select(LineEndpoint).where(
        LineEndpoint.provider == provider, LineEndpoint.line_key == line_key
    )
    return session.execute(stmt).scalar_one_or_none()


def _load_line(session: Session, endpoint: LineEndpoint) -> ProtectedLine:
    line = session.execute(
        select(ProtectedLine).where(
            ProtectedLine.tenant_id == endpoint.tenant_id, ProtectedLine.id == endpoint.line_id
        )
    ).scalar_one_or_none()
    if line is None:
        raise _rejected()
    return line


def _record_nonce(session: Session, line: ProtectedLine, nonce: str) -> None:
    try:
        with session.begin_nested():
            session.add(WebhookReceipt(tenant_id=line.tenant_id, line_id=line.id, nonce=nonce))
    except IntegrityError as exc:
        raise ApiError("replayed webhook", code="WEBHOOK_REPLAY", status_code=409) from exc


def _check_addressed_to_line(
    event: ProviderCallEvent, line: ProtectedLine, endpoint: LineEndpoint
) -> None:
    if (
        event.event_type == ProviderEventType.CALL_RECEIVED
        and event.to_number is None
        and event.endpoint_id is None
    ):
        raise ApiError("event does not identify a line", code="MALFORMED_EVENT")
    if event.to_number is not None and event.to_number != line.e164:
        raise ApiError("event addressed to a different line", code="LINE_MISMATCH")
    if (
        event.endpoint_id is not None
        and endpoint.endpoint_id is not None
        and event.endpoint_id != endpoint.endpoint_id
    ):
        raise ApiError("event addressed to a different endpoint", code="LINE_MISMATCH")


def _dispatch(
    session: Session,
    settings: Settings,
    line: ProtectedLine,
    endpoint: LineEndpoint,
    event: ProviderCallEvent,
    adapter: ProviderAdapter,
) -> IngestOutcome:
    kind = event.event_type
    if kind in (ProviderEventType.REGISTRATION_FAILED, ProviderEventType.REGISTRATION_OK):
        return handle_registration(session, line, endpoint, event, adapter)
    if event.call_id is None:
        raise ApiError("call_id required", code="MALFORMED_EVENT")
    if kind == ProviderEventType.CALL_RECEIVED:
        if event.direction != CallDirection.INBOUND.value:
            raise ApiError("only inbound calls are handled", code="UNSUPPORTED_DIRECTION")
        return handle_call_received(session, settings, line, event, adapter)
    if kind in _TRANSITIONS:
        return apply_transition(session, line, event)
    if kind == ProviderEventType.CALLER_UTTERANCE:
        return handle_utterance(session, settings, line, event, adapter, current_llm())
    return handle_screening_failure(session, settings, line, event, adapter)


def process_webhook(
    session: Session,
    settings: Settings,
    provider: str,
    line_key: str,
    headers: Mapping[str, str],
    body: bytes,
) -> IngestOutcome:
    adapter = get_adapter(provider)
    if adapter is None:
        raise ApiError("unknown provider", code="UNKNOWN_PROVIDER", status_code=404)
    endpoint = resolve_endpoint(session, provider, line_key)
    if endpoint is None:
        logger.warning("webhook rejected: unknown line key provider=%s", provider)
        raise _rejected()
    line = _load_line(session, endpoint)
    try:
        nonce = adapter.verify_webhook(
            headers, body, endpoint.webhook_secret, settings.webhook_replay_window_seconds
        )
    except WebhookVerificationError as exc:
        logger.warning("webhook rejected line=%s reason=%s", line.id, exc.code)
        raise _rejected() from exc
    _record_nonce(session, line, nonce)
    try:
        event = adapter.parse_event(body)
    except MalformedEventError as exc:
        logger.warning("webhook malformed line=%s", line.id)
        raise ApiError("malformed event", code="MALFORMED_EVENT") from exc
    _check_addressed_to_line(event, line, endpoint)
    is_registration = event.event_type.value.startswith("registration.")
    if not is_registration and line.verification_status != LineVerificationStatus.VERIFIED.value:
        raise ForbiddenError("line is not verified", code="LINE_NOT_VERIFIED")
    outcome = _dispatch(session, settings, line, endpoint, event, adapter)
    touch_health(session, line.tenant_id, provider)
    return outcome
