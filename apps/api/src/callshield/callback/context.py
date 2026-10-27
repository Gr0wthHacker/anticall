"""DB -> plain facts for the pure gates. Everything missing stays None (gates fail closed)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from callshield.auth.scoping import scoped_select
from callshield.callback.authorization import authorization_facts
from callshield.callback.blocklist import is_blocked_by_instance, is_blocked_by_user
from callshield.callback.caps import caps_snapshot, limits_for
from callshield.callback.config import (
    CallbackSettings,
    EffectiveSettings,
    merge_effective,
)
from callshield.callback.gates import AuthorizationMode, GateInputs
from callshield.callback.keys import DestinationKeyer
from callshield.callback.lookup import LineTypeLookup
from callshield.callback.timeutil import resolve_timezone
from callshield.core.phone import InvalidPhoneNumberError, mask_phone, normalize_e164
from callshield.db.models.callback_ext import CallbackAuthorization, CallbackSettingsRow
from callshield.db.models.defend import CallerObservation, CallSession, ProtectedLine
from callshield.db.models.defend_ext import CallRouting
from callshield.domain.enums import CallDirection

_STRIPPED_FLAGS = ("stripped", "attestation_stripped", "forwarded_without_identity")


@dataclass(frozen=True)
class InboundContext:
    call: CallSession
    line: ProtectedLine
    destination: str | None  # E.164, derived ONLY from the logged displayed caller ID
    attestation_level: str | None
    attestation_stripped: bool


@dataclass(frozen=True)
class UserSettingsView:
    effective: EffectiveSettings
    timezone: str | None


def load_user_settings(
    session: Session, base: CallbackSettings, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> UserSettingsView:
    rows = {
        r.scope_key: r
        for r in session.execute(
            scoped_select(CallbackSettingsRow, tenant_id).where(
                CallbackSettingsRow.scope_key.in_(["TENANT", f"USER:{user_id}"])
            )
        ).scalars()
    }
    layers: list[dict[str, Any]] = [
        rows[k].overrides for k in ("TENANT", f"USER:{user_id}") if k in rows
    ]
    user_row = rows.get(f"USER:{user_id}")
    return UserSettingsView(merge_effective(base, *layers), user_row.timezone if user_row else None)


def _displayed_number(routing: CallRouting | None, obs: CallerObservation | None) -> str | None:
    candidates = {
        normalized
        for raw in (
            routing.from_displayed if routing else None,
            obs.displayed_caller_id if obs else None,
        )
        if raw and (normalized := _safe_e164(raw)) is not None
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _safe_e164(raw: str) -> str | None:
    try:
        return normalize_e164(raw)
    except InvalidPhoneNumberError:
        return None


def load_inbound(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, inbound_call_id: uuid.UUID
) -> InboundContext | None:
    """None when the call is not an inbound call of this user's line in this tenant."""
    call = session.execute(
        scoped_select(CallSession, tenant_id).where(CallSession.id == inbound_call_id)
    ).scalar_one_or_none()
    if call is None or call.direction != CallDirection.INBOUND.value or call.line_id is None:
        return None
    line = session.execute(
        scoped_select(ProtectedLine, tenant_id).where(ProtectedLine.id == call.line_id)
    ).scalar_one_or_none()
    if line is None or line.owner_user_id != user_id:
        return None
    routing = session.execute(
        scoped_select(CallRouting, tenant_id).where(CallRouting.session_id == call.id)
    ).scalar_one_or_none()
    obs = session.execute(
        scoped_select(CallerObservation, tenant_id)
        .where(CallerObservation.session_id == call.id)
        .order_by(CallerObservation.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    meta = dict(obs.attestation_metadata or {}) if obs else {}
    level = meta.get("attestation_level") if obs else None
    stripped = any(bool(meta.get(flag)) for flag in _STRIPPED_FLAGS)
    return InboundContext(call, line, _displayed_number(routing, obs), level, stripped)


def build_gate_inputs(
    session: Session,
    *,
    now: datetime,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    inbound: InboundContext | None,
    view: UserSettingsView,
    keyer: DestinationKeyer,
    lookup: LineTypeLookup,
    authorization: CallbackAuthorization | None,
    mode: AuthorizationMode,
) -> tuple[GateInputs, str | None]:
    """Returns (inputs, destination_key). Missing pieces are left None -> gate fails closed."""
    if inbound is None:
        return GateInputs(now=now, authorization_mode=mode), None
    key = keyer.key(inbound.destination) if inbound.destination else None
    geography = lookup.lookup(inbound.destination) if inbound.destination else None
    dest_name = geography.timezone if geography else None
    caps = limits = blocked_user = blocked_inst = None
    if key is not None:
        caps = caps_snapshot(session, tenant_id, user_id, inbound.call.id, key, now)
        limits = limits_for(view.effective)
        blocked_user = is_blocked_by_user(session, tenant_id, user_id, key)
        blocked_inst = is_blocked_by_instance(session, key)
    user_tz_name = view.timezone or (inbound.line.routing_config or {}).get("timezone")
    return (
        GateInputs(
            now=now,
            inbound_found=True,
            destination_present=inbound.destination is not None,
            inbound_at=inbound.call.started_at,
            attestation_level=inbound.attestation_level,
            attestation_stripped=inbound.attestation_stripped,
            window_hours=view.effective.callback_window_hours,
            authorization=authorization_facts(authorization, user_id, inbound.call.id),
            blocked_by_user=blocked_user,
            blocked_by_instance=blocked_inst,
            caps=caps,
            limits=limits,
            user_tz=resolve_timezone(user_tz_name if isinstance(user_tz_name, str) else None),
            destination_geography_provided=dest_name is not None,
            destination_tz=resolve_timezone(dest_name),
            authorization_mode=mode,
        ),
        key,
    )


def masked_destination(inbound: InboundContext) -> str:
    return mask_phone(inbound.destination)
