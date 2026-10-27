"""Platform extension tables (own module so shared model files stay untouched)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from callshield.core.clock import utcnow
from callshield.db.base import Base, JSONType, TenantOwned, UTCDateTime, id_column


class Acknowledgment(TenantOwned, Base):
    """A user's acceptance of one exact acknowledgment text (docs/legal/acknowledgment-v1.json).

    Append-only. A new text version produces a new digest and therefore a new row.
    """

    __tablename__ = "acknowledgments"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "text_sha256"),)

    id: Mapped[uuid.UUID] = id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    acknowledgment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(100), nullable=False)
    item_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
