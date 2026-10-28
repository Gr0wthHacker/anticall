"""Publish ARI link health into the existing telephony health model (per-tenant rows read by
GET /v1/health/telephony). Only tenants that have an ARI-provider line are touched, and a SIP
registration failure recorded by the ingest path is never overwritten by an "OK" link status."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from callshield.core.clock import utcnow
from callshield.db.models.defend_ext import LineEndpoint
from callshield.defend.health import STATUS_DEGRADED, STATUS_OK, get_health_row
from callshield.telephony.asterisk.ari_client import AriHealth
from callshield.telephony.asterisk.events import PROVIDER

logger = logging.getLogger("callshield.asterisk.health")
_LINK_DETAILS = ("", "connected", "not connected yet")


def _owns_row(detail: str) -> bool:
    return detail in _LINK_DETAILS or detail.startswith("ari_") or detail == "stopped"


def write_link_health(factory: sessionmaker[Session], health: AriHealth) -> int:
    """Returns the number of tenants updated."""
    status = STATUS_OK if health.status == STATUS_OK else STATUS_DEGRADED
    with factory() as session:
        tenants = session.execute(
            select(LineEndpoint.tenant_id).where(LineEndpoint.provider == PROVIDER).distinct()
        ).scalars()
        count = 0
        for tenant_id in list(tenants):
            row = get_health_row(session, tenant_id, PROVIDER)
            if status == STATUS_OK and row.status == STATUS_DEGRADED and not _owns_row(row.detail):
                continue  # a SIP registration failure outranks "the control link is up"
            row.status = status
            row.detail = "" if status == STATUS_OK else health.detail[:200]
            row.last_event_at = utcnow()
            row.updated_at = utcnow()
            count += 1
        session.commit()
        return count


class HealthPublisher:
    """Install with `client.add_health_listener(publisher.on_health)`."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._tasks: set[asyncio.Task[int]] = set()

    def on_health(self, health: AriHealth) -> None:
        task = asyncio.ensure_future(asyncio.to_thread(write_link_health, self._factory, health))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[int]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("health publish failed type=%s", type(task.exception()).__name__)

    async def publish(self, health: AriHealth) -> int:
        return await asyncio.to_thread(write_link_health, self._factory, health)
