"""Worker loop: claim -> dispatch -> complete/fail."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import time

from sqlalchemy.orm import Session, sessionmaker

from callshield.core.config import get_settings
from callshield.db.session import bind_tenant, make_engine, make_session_factory, session_scope
from callshield.worker.queue import claim_next, complete, fail, get_handler

logger = logging.getLogger("callshield.worker")


def run_one(factory: sessionmaker[Session], kinds: list[str] | None = None) -> bool:
    """Process at most one job. Returns True if a job was claimed."""
    with session_scope(factory) as claim_session:
        job = claim_next(claim_session, kinds)
        if job is None:
            return False
        job_id, tenant_id, kind = job.id, job.tenant_id, job.kind
    handler = get_handler(kind)
    with session_scope(factory) as session:
        bind_tenant(session, tenant_id)
        current = session.get(type(job), job_id)
        assert current is not None
        if handler is None:
            fail(session, current, f"no handler registered for {kind}")
            return True
        try:
            with session.begin_nested():
                handler(session, current)
        except Exception as exc:
            logger.warning("job %s (%s) failed: %s", job_id, kind, type(exc).__name__)
            fail(session, current, f"{type(exc).__name__}: {exc}")
        else:
            complete(session, current)
    return True


def load_handler_modules() -> None:
    """Import callshield.worker.handlers.* so @job_handler decorators register."""
    try:
        package = importlib.import_module("callshield.worker.handlers")
    except ModuleNotFoundError:
        return
    for info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        importlib.import_module(info.name)


def run_forever(poll_interval: float = 2.0) -> None:  # pragma: no cover - loop
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.db_url, settings.db_echo))
    load_handler_modules()
    while True:
        if not run_one(factory):
            time.sleep(poll_interval)
