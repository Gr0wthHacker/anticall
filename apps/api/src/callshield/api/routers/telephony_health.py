"""GET /v1/health/telephony: adapter and SIP registration status for the caller's tenant."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from callshield.db.models.defend_ext import TelephonyHealth
from callshield.defend.deps import Reader
from callshield.defend.schemas import TelephonyHealthOut
from callshield.telephony.registry import adapter_names, get_adapter

router = APIRouter(tags=["health"])


@router.get("/v1/health/telephony", response_model=TelephonyHealthOut)
def telephony_health(ctx: Reader) -> TelephonyHealthOut:
    rows = ctx.session.execute(
        select(TelephonyHealth)
        .where(TelephonyHealth.tenant_id == ctx.tenant_id)
        .order_by(TelephonyHealth.provider)
    ).scalars()
    providers = [
        {
            "provider": r.provider,
            "status": r.status,
            "detail": r.detail,
            "last_event_at": r.last_event_at.isoformat() if r.last_event_at else None,
        }
        for r in rows
    ]
    adapters = []
    for name in adapter_names():
        adapter = get_adapter(name)
        if adapter is not None:
            adapters.append({"provider": name, **adapter.health()})
    degraded = any(p["status"] == "DEGRADED" for p in providers)
    return TelephonyHealthOut(
        overall="DEGRADED" if degraded else ("OK" if providers else "UNKNOWN"),
        providers=providers,
        adapters=adapters,
    )
