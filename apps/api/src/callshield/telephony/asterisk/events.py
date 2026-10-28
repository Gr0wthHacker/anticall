"""ARI events -> normalized `ProviderCallEvent`s -> the shared authenticated ingest tail.

ARI arrives over an authenticated local WebSocket (Basic auth), not HMAC webhooks. The tenant
is resolved ONLY from a protected line that matches the dialed number (and, when the line
configures one, the endpoint) or from a call this process already stored; payload fields can
select among lines but never name a tenant. Idempotency, ledger, incident and job creation
reuse `defend.ingest.dispatch_authenticated_event`.

STIR/SHAKEN: an Identity header or `attest` value is trusted ONLY when Asterisk's own
verification reports success (verstat / verify_result). Otherwise it is treated as missing,
so a forged Identity header can never unlock the callback eligibility gate.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from callshield.core.config import Settings
from callshield.core.errors import ApiError
from callshield.core.phone import mask_phone
from callshield.db.models.defend import CallSession, ProtectedLine
from callshield.db.models.defend_ext import LineEndpoint
from callshield.db.session import bind_tenant
from callshield.defend.health import handle_registration
from callshield.defend.ingest import dispatch_authenticated_event
from callshield.defend.lifecycle import IngestOutcome
from callshield.telephony.adapters import (
    build_event,
    clean_display,
    parse_timestamp,
    to_e164,
)
from callshield.telephony.base import (
    MalformedEventError,
    ProviderAdapter,
    ProviderCallEvent,
    ProviderEventType,
)
from callshield.telephony.registry import get_adapter

from .ari_client import AriApi, AriError

logger = logging.getLogger("callshield.asterisk.events")

PROVIDER = "ari"
INBOUND_ARG = "inbound"
VAR_IDENTITY = "PJSIP_HEADER(read,Identity)"
VAR_ATTEST = "STIR_SHAKEN(0,attestation)"
VAR_VERIFY = "STIR_SHAKEN(0,verify_result)"
VAR_VERSTAT = "verstat"
_PASSED = re.compile(r"TN-Validation-Passed|^(success|passed|valid)$", re.IGNORECASE)

_SIMPLE_TYPES: dict[str, ProviderEventType] = {
    "StasisStart": ProviderEventType.CALL_RECEIVED,
    # StasisEnd is NOT a call end (continue() into the dialplan also produces it)
    "ChannelDestroyed": ProviderEventType.CALL_ENDED,
    "ChannelHangupRequest": ProviderEventType.CALL_ENDED,
    "BlindTransfer": ProviderEventType.CALL_TRANSFERRED,
    "AttendedTransfer": ProviderEventType.CALL_TRANSFERRED,
}


def event_args(data: Mapping[str, Any]) -> list[str]:
    raw = data.get("args")
    return [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []


def is_inbound_start(data: Mapping[str, Any]) -> bool:
    args = event_args(data)
    return data.get("type") == "StasisStart" and bool(args) and args[0] == INBOUND_ARG


def channel_id(data: Mapping[str, Any]) -> str | None:
    channel = data.get("channel")
    cid = channel.get("id") if isinstance(channel, dict) else None
    return cid if isinstance(cid, str) and cid else None


def dtmf_digit(data: Mapping[str, Any]) -> str | None:
    digit = data.get("digit")
    if data.get("type") == "ChannelDtmfReceived" and isinstance(digit, str) and len(digit) == 1:
        return digit
    return None


def _state_type(data: Mapping[str, Any]) -> ProviderEventType | None:
    kind = data.get("type")
    channel = data.get("channel")
    if kind == "ChannelStateChange" and isinstance(channel, dict) and channel.get("state") == "Up":
        return ProviderEventType.CALL_ANSWERED
    if kind in ("PeerStatusChange", "EndpointStateChange"):
        return _registration_type(data)
    return None


def _registration_type(data: Mapping[str, Any]) -> ProviderEventType | None:
    peer = data.get("peer") if isinstance(data.get("peer"), dict) else {}
    endpoint = data.get("endpoint") if isinstance(data.get("endpoint"), dict) else {}
    status = str((peer or {}).get("peer_status") or (endpoint or {}).get("state") or "").lower()
    if status in ("unreachable", "unregistered", "rejected", "offline"):
        return ProviderEventType.REGISTRATION_FAILED
    if status in ("reachable", "registered", "online"):
        return ProviderEventType.REGISTRATION_OK
    return None


def _endpoint_of(data: Mapping[str, Any]) -> str | None:
    endpoint = data.get("endpoint")
    if isinstance(endpoint, dict) and endpoint.get("resource"):
        return str(endpoint["resource"])[:120]
    channel = data.get("channel")
    name = channel.get("name") if isinstance(channel, dict) else None
    if isinstance(name, str) and "/" in name:
        return name.split("/", 1)[1].rsplit("-", 1)[0][:120] or None
    return None


def _dialed_number(data: Mapping[str, Any]) -> str | None:
    args = event_args(data)
    if len(args) > 1 and args[1]:
        return to_e164(args[1])
    channel = data.get("channel")
    dialplan = channel.get("dialplan") if isinstance(channel, dict) else None
    return to_e164(dialplan.get("exten")) if isinstance(dialplan, dict) else None


def _int_arg(args: list[str], key: str) -> int:
    for item in args:
        name, _, value = item.partition("=")
        if name == key and value.isdigit():
            return min(int(value), 64)
    return 0


def map_ari_event(
    data: Mapping[str, Any], attestation: Mapping[str, Any] | None = None
) -> ProviderCallEvent | None:
    """Normalize one ARI event; None when it is not one we model. Raises MalformedEventError
    for shapes that claim to be modeled events but cannot be normalized."""
    kind = str(data.get("type"))
    etype = _SIMPLE_TYPES.get(kind) or _state_type(data)
    if etype is None or (kind == "StasisStart" and not is_inbound_start(data)):
        return None
    cid = channel_id(data)
    if etype.value.startswith("registration."):
        if _endpoint_of(data) is None:
            raise MalformedEventError("registration event without endpoint")
    elif cid is None:
        raise MalformedEventError("event without channel")
    channel = data.get("channel") if isinstance(data.get("channel"), dict) else {}
    caller = (channel or {}).get("caller") or {}
    args = event_args(data)
    is_start = etype == ProviderEventType.CALL_RECEIVED
    stamp = data.get("timestamp")
    return build_event(
        provider=PROVIDER,
        event_id=f"{kind}:{cid or _endpoint_of(data)}:{stamp}",
        event_type=etype,
        call_id=cid,
        to_number=_dialed_number(data) if is_start else None,
        endpoint_id=_endpoint_of(data) if is_start or cid is None else None,
        from_number=clean_display(caller.get("number")) if is_start else None,
        attestation=dict(attestation or {}) if is_start else {},
        hop_count=_int_arg(args, "hops") if is_start else 0,
        occurred_at=parse_timestamp(stamp),
        detail=("hangup_request" if kind == "ChannelHangupRequest" else "")[:200],
    )


# ------------------------------------------------------------------ attestation
async def _read_var(ari: AriApi, cid: str, name: str) -> str | None:
    try:
        body = await ari.request("GET", f"/channels/{cid}/variable", params={"variable": name})
    except AriError:
        return None
    value = body.get("value") if isinstance(body, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def build_attestation(
    identity: str | None, attest: str | None, verify: str | None, verstat: str | None
) -> dict[str, Any]:
    """Trust the claimed level only when Asterisk's verification says it passed."""
    passed = any(v and _PASSED.search(v) for v in (verify, verstat))
    if not passed:
        marker = verstat or verify
        return {"verstat": marker} if marker else {}
    out: dict[str, Any] = {}
    for key, value in (
        ("identity", identity),
        ("attest", attest),
        ("verstat", verstat or verify),
    ):
        if value:
            out[key] = value
    return out


async def fetch_attestation(ari: AriApi, cid: str) -> dict[str, Any]:
    names = (VAR_IDENTITY, VAR_ATTEST, VAR_VERIFY, VAR_VERSTAT)
    values = await asyncio.gather(*(_read_var(ari, cid, n) for n in names))
    return build_attestation(*values)


# ------------------------------------------------------------------ ingestor
@dataclass
class IngestResult:
    status: str  # accepted | duplicate | ignored | rejected
    outcome: IngestOutcome | None = None
    reason: str = ""
    tenant_id: uuid.UUID | None = None  # from the matched line only
    line_id: uuid.UUID | None = None


class AriEventIngestor:
    """Synchronous, DB-backed. Call via `aingest` from the event loop."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        adapter: ProviderAdapter | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._adapter = adapter

    def _adapter_or_default(self) -> ProviderAdapter:
        adapter = self._adapter or get_adapter(PROVIDER)
        if adapter is None:
            raise RuntimeError("ari adapter is not registered")
        return adapter

    async def aingest(self, event: ProviderCallEvent) -> IngestResult:
        return await asyncio.to_thread(self.ingest, event)

    def ingest(self, event: ProviderCallEvent) -> IngestResult:
        with self._factory() as session:
            try:
                result = self._ingest(session, event)
                session.commit()
                return result
            except ApiError as exc:
                session.rollback()
                logger.warning("ari event rejected code=%s type=%s", exc.code, event.event_type)
                return IngestResult("rejected", reason=exc.code)

    def _ingest(self, session: Session, event: ProviderCallEvent) -> IngestResult:
        adapter = self._adapter_or_default()
        if event.event_type.value.startswith("registration."):
            return self._registration(session, event, adapter)
        found = _resolve_line(session, event)
        if found is None:
            logger.warning(
                "ari event matches no unique protected line to=%s", mask_phone(event.to_number)
            )
            return IngestResult("ignored", reason="NO_UNIQUE_LINE")
        line, endpoint = found
        bind_tenant(session, line.tenant_id)
        outcome = dispatch_authenticated_event(
            session, self._settings, line, endpoint, adapter, event
        )
        return IngestResult(outcome.status, outcome, "", line.tenant_id, line.id)

    def _registration(
        self, session: Session, event: ProviderCallEvent, adapter: ProviderAdapter
    ) -> IngestResult:
        rows = session.execute(
            select(ProtectedLine, LineEndpoint)
            .join(LineEndpoint, LineEndpoint.line_id == ProtectedLine.id)
            .where(LineEndpoint.provider == PROVIDER, LineEndpoint.endpoint_id == event.endpoint_id)
        ).all()
        if not rows:
            return IngestResult("ignored", reason="NO_LINE_FOR_ENDPOINT")
        outcomes = [handle_registration(session, ln, ep, event, adapter) for ln, ep in rows]
        return IngestResult(outcomes[0].status, outcomes[0])


def _resolve_line(
    session: Session, event: ProviderCallEvent
) -> tuple[ProtectedLine, LineEndpoint] | None:
    """Exactly one ARI-enabled line, from the dialed number or from a stored call."""
    stmt = select(ProtectedLine, LineEndpoint).join(
        LineEndpoint,
        (LineEndpoint.line_id == ProtectedLine.id)
        & (LineEndpoint.tenant_id == ProtectedLine.tenant_id),
    )
    if event.to_number is not None:
        stmt = stmt.where(LineEndpoint.provider == PROVIDER, ProtectedLine.e164 == event.to_number)
    elif event.call_id is not None:
        stmt = stmt.join(
            CallSession,
            (CallSession.line_id == ProtectedLine.id)
            & (CallSession.tenant_id == ProtectedLine.tenant_id),
        ).where(CallSession.provider == PROVIDER, CallSession.provider_call_id == event.call_id)
    else:
        return None
    rows = session.execute(stmt).all()
    unique = {(r[0].id, r[1].id) for r in rows}
    if len(unique) != 1:
        return None
    return rows[0][0], rows[0][1]
