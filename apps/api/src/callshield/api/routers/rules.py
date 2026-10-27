"""Routing rules (D-003). DELETE disables (audited); rules are never hard-deleted."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_get_or_404
from callshield.db.models.defend import ProtectedLine, RoutingRule
from callshield.defend.deps import Reader, Writer
from callshield.defend.schemas import RuleCreate, RuleOut, RulePut

router = APIRouter(tags=["rules"])


def _out(rule: RoutingRule) -> RuleOut:
    return RuleOut(
        id=rule.id,
        line_id=rule.line_id,
        priority=rule.priority,
        matcher=rule.matcher,
        action=rule.action,
        enabled=rule.enabled,
        created_at=rule.created_at,
    )


@router.post("/v1/rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, ctx: Writer) -> RuleOut:
    line = scoped_get_or_404(ctx.session, ProtectedLine, body.line_id, ctx.tenant_id)
    rule = RoutingRule(
        tenant_id=ctx.tenant_id,
        line_id=line.id,
        priority=body.priority,
        matcher=body.matcher.model_dump(exclude_defaults=True),
        action=body.action.value,
        enabled=body.enabled,
    )
    ctx.session.add(rule)
    ctx.session.flush()
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "rule.created",
        {"rule_id": str(rule.id), "line_id": str(line.id), "action": rule.action},
    )
    return _out(rule)


@router.get("/v1/rules", response_model=list[RuleOut])
def list_rules(
    ctx: Reader,
    line_id: uuid.UUID | None = None,
    include_disabled: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[RuleOut]:
    stmt = select(RoutingRule).where(RoutingRule.tenant_id == ctx.tenant_id)
    if line_id is not None:
        stmt = stmt.where(RoutingRule.line_id == line_id)
    if not include_disabled:
        stmt = stmt.where(RoutingRule.enabled.is_(True))
    rows = ctx.session.execute(
        stmt.order_by(RoutingRule.priority, RoutingRule.id).limit(limit)
    ).scalars()
    return [_out(r) for r in rows]


@router.get("/v1/rules/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: uuid.UUID, ctx: Reader) -> RuleOut:
    return _out(scoped_get_or_404(ctx.session, RoutingRule, rule_id, ctx.tenant_id))


@router.put("/v1/rules/{rule_id}", response_model=RuleOut)
def put_rule(rule_id: uuid.UUID, body: RulePut, ctx: Writer) -> RuleOut:
    rule = scoped_get_or_404(ctx.session, RoutingRule, rule_id, ctx.tenant_id)
    before = {"priority": rule.priority, "action": rule.action, "enabled": rule.enabled}
    rule.priority = body.priority
    rule.matcher = body.matcher.model_dump(exclude_defaults=True)
    rule.action = body.action.value
    rule.enabled = body.enabled
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "rule.updated",
        {"rule_id": str(rule.id), "before": before, "after_action": rule.action},
    )
    return _out(rule)


@router.delete("/v1/rules/{rule_id}", response_model=RuleOut)
def disable_rule(rule_id: uuid.UUID, ctx: Writer) -> RuleOut:
    rule = scoped_get_or_404(ctx.session, RoutingRule, rule_id, ctx.tenant_id)
    rule.enabled = False
    record_audit(
        ctx.session,
        ctx.tenant_id,
        actor_for_user(ctx.principal.user_id),
        "rule.disabled",
        {"rule_id": str(rule.id)},
    )
    return _out(rule)
