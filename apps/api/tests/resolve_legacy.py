"""Service-level harness for the RETIRED opt-out outbound routes (not a test module).

`POST .../contact-authorizations` and `POST .../start-contact` now return 410
(OUTBOUND_OPT_OUT_REMOVED, see ADR 0006), but the reusable libraries they drove
(`resolve.authorizations`, `resolve.service.start_contact`, `resolve.policy`, the IVR
navigator and agent tools) stay. These helpers call those libraries directly and mimic the
old HTTP response shape so the existing policy / cooldown / caps / IVR / agent assertions in
test_resolve_* keep running against the reusable pieces.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request

from callshield.core.errors import ApiError
from callshield.db.session import bind_tenant, make_session_factory
from callshield.domain.enums import ContactScope, Role
from callshield.resolve import authorizations, cases
from callshield.resolve.deps import get_dialer, get_dialogue_model
from callshield.resolve.dialer import OutboundDialer
from callshield.resolve.service import StartContactCommand, start_contact

if TYPE_CHECKING:
    from .resolve_support import Env


@dataclass
class LegacyResponse:
    status_code: int
    _body: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self._body

    @property
    def text(self) -> str:
        return str(self._body)


def _dialer(env: Env) -> OutboundDialer:
    app = env.client.app
    assert isinstance(app, FastAPI)
    override = app.dependency_overrides.get(get_dialer)
    if override is not None:
        dialer: OutboundDialer = override()
        return dialer
    return get_dialer(cast(Request, SimpleNamespace(app=app)))


def _error(exc: ApiError) -> LegacyResponse:
    body = {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return LegacyResponse(exc.status_code, body)


def legacy_authorize(
    env: Env,
    case_id: str,
    body: dict[str, Any],
    key: str | None,
    *,
    user_id: uuid.UUID | None = None,
    role: Role = Role.HOUSEHOLD_ADMIN,
) -> LegacyResponse:
    with make_session_factory(env.engine)() as session:
        bind_tenant(session, env.user.tenant_id)
        try:
            case = cases.get_case(session, env.user.tenant_id, uuid.UUID(case_id))
            auth, created = authorizations.create_authorization(
                session,
                env.settings,
                env.user.tenant_id,
                user_id or env.user.user_id,
                role,
                case,
                uuid.UUID(str(body["destination_id"])),
                scope=ContactScope(body.get("scope", ContactScope.OUTBOUND_CALL_ONCE)),
                ttl_seconds=int(body.get("ttl_seconds", authorizations.DEFAULT_TTL_SECONDS)),
                idempotency_key=key or body.get("idempotency_key") or "",
                basis=str(body.get("basis", "")),
            )
            session.commit()
        except ApiError as exc:
            session.rollback()
            return _error(exc)
        out = {
            "id": str(auth.id),
            "case_id": str(auth.case_id),
            "destination_id": str(auth.destination_id),
            "scope": auth.scope,
            "expires_at": auth.expires_at.isoformat(),
            "consumed_at": auth.consumed_at.isoformat() if auth.consumed_at else None,
            "created": created,
        }
        return LegacyResponse(201, out)


def legacy_start(
    env: Env,
    case_id: str,
    body: dict[str, Any],
    key: str | None,
    *,
    user_id: uuid.UUID | None = None,
    role: Role = Role.HOUSEHOLD_ADMIN,
) -> LegacyResponse:
    with make_session_factory(env.engine)() as session:
        bind_tenant(session, env.user.tenant_id)
        auth_id = body.get("authorization_id")
        cmd = StartContactCommand(
            tenant_id=env.user.tenant_id,
            user_id=user_id or env.user.user_id,
            role=role,
            case_id=uuid.UUID(case_id),
            destination_id=uuid.UUID(str(body["destination_id"])),
            authorization_id=uuid.UUID(str(auth_id)) if auth_id else None,
            idempotency_key=key or body.get("idempotency_key"),
            reason=str(body.get("reason", "")),
            mode=str(body.get("mode", "AI")),
        )
        try:
            cases.get_case(session, env.user.tenant_id, cmd.case_id)
            result = start_contact(session, env.settings, _dialer(env), cmd, get_dialogue_model())
            session.commit()
        except ApiError as exc:
            session.rollback()
            return _error(exc)
        if not result.decision.allowed or result.attempt is None:
            reasons = result.decision.reasons
            details = {"reasons": [r.as_dict() for r in reasons]}
            code = reasons[0].code if reasons else "POLICY_BLOCKED"
            return LegacyResponse(
                403,
                {"error": {"code": code, "message": "start-contact refused", "details": details}},
            )
        case = cases.get_case(session, env.user.tenant_id, cmd.case_id)
        return LegacyResponse(
            200,
            {
                "attempt_id": str(result.attempt.id),
                "replayed": result.replayed,
                "outcome": result.attempt.outcome,
                "case_state": case.status,
                "mode": result.attempt.mode,
                "note": "Acknowledgment is a claim; it is not confirmation of suppression.",
            },
        )
