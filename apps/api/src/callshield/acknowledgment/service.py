"""Record and look up a user's acknowledgment of the current versioned text.

The server decides what the "current" text is and its digest; the client can only say which
items it ticked and which digest it was shown. All items must be true.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from callshield.acknowledgment.content import ACKNOWLEDGMENT_V1, ACKNOWLEDGMENT_VERSION
from callshield.audit import actor_for_user, record_audit
from callshield.auth.scoping import scoped_select
from callshield.core.clock import utcnow
from callshield.core.errors import ConflictError, UnprocessableError
from callshield.core.hashing import sha256_json
from callshield.db.models.platform_ext import Acknowledgment


def item_ids() -> list[str]:
    return [str(item["id"]) for item in ACKNOWLEDGMENT_V1["items"]]


def current_text_sha256() -> str:
    """Digest of exactly what the user is shown: the preamble and every item id + text."""
    shown = {
        "preamble": ACKNOWLEDGMENT_V1["preamble"],
        "items": [{"id": i["id"], "text": i["text"]} for i in ACKNOWLEDGMENT_V1["items"]],
    }
    return sha256_json(shown)


def current_document() -> dict[str, Any]:
    return {
        "acknowledgment_version": ACKNOWLEDGMENT_VERSION,
        "text_sha256": current_text_sha256(),
        **ACKNOWLEDGMENT_V1,
    }


def _current_row(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> Acknowledgment | None:
    stmt = scoped_select(Acknowledgment, tenant_id).where(
        Acknowledgment.user_id == user_id, Acknowledgment.text_sha256 == current_text_sha256()
    )
    return session.execute(stmt).scalar_one_or_none()


def has_acknowledged(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return _current_row(session, tenant_id, user_id) is not None


def history(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Acknowledgment]:
    stmt = (
        scoped_select(Acknowledgment, tenant_id)
        .where(Acknowledgment.user_id == user_id)
        .order_by(Acknowledgment.accepted_at.desc(), Acknowledgment.id)
    )
    return list(session.execute(stmt).scalars())


def _check_items(accepted: dict[str, bool]) -> None:
    expected = set(item_ids())
    unknown = sorted(set(accepted) - expected)
    unchecked = sorted(i for i in expected if accepted.get(i) is not True)
    if unknown or unchecked:
        raise UnprocessableError(
            "every acknowledgment item must be explicitly checked",
            code="ACKNOWLEDGMENT_INCOMPLETE",
            details={"unchecked": unchecked, "unknown": unknown},
        )


def accept(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    accepted: dict[str, bool],
    shown_sha256: str,
) -> tuple[Acknowledgment, bool]:
    """Store the acknowledgment. Returns (row, created); a repeat is an idempotent no-op."""
    if shown_sha256 != current_text_sha256():
        raise ConflictError(
            "the acknowledgment text changed; review the current version",
            code="ACKNOWLEDGMENT_STALE",
            details={"current_text_sha256": current_text_sha256()},
        )
    _check_items(accepted)
    existing = _current_row(session, tenant_id, user_id)
    if existing is not None:
        return existing, False
    row = Acknowledgment(
        tenant_id=tenant_id,
        user_id=user_id,
        acknowledgment_version=ACKNOWLEDGMENT_VERSION,
        text_sha256=current_text_sha256(),
        operator_id=str(tenant_id),  # the operator of this installation is the tenant
        item_ids=item_ids(),
        accepted_at=utcnow(),
    )
    session.add(row)
    record_audit(
        session,
        tenant_id,
        actor_for_user(user_id),
        "acknowledgment.accepted",
        {"acknowledgment_version": ACKNOWLEDGMENT_VERSION, "text_sha256": row.text_sha256},
    )
    session.flush()
    return row, True
