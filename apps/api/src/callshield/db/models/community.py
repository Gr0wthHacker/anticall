"""Community tables (post-MVP, feature-gated off by default)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from callshield.db.base import Base, TenantOwned, created_column, id_column
from callshield.domain.enums import CampaignReviewState


class CandidateCampaign(TenantOwned, Base):
    __tablename__ = "candidate_campaigns"

    id: Mapped[uuid.UUID] = id_column()
    features_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_status: Mapped[str] = mapped_column(
        String(32), default=CampaignReviewState.CANDIDATE.value
    )
    created_at: Mapped[datetime] = created_column()


class CampaignLink(TenantOwned, Base):
    __tablename__ = "campaign_links"

    id: Mapped[uuid.UUID] = id_column()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_campaigns.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="correlation_engine")
    review_state: Mapped[str] = mapped_column(
        String(32), default=CampaignReviewState.CANDIDATE.value
    )
    created_at: Mapped[datetime] = created_column()
