"""Meta endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from callshield import __version__
from callshield.auth.deps import get_app_settings
from callshield.core.config import Settings

router = APIRouter(tags=["meta"])


class CapabilitiesResponse(BaseModel):
    version: str
    capabilities: dict[str, bool]


@router.get("/v1/meta/capabilities", response_model=CapabilitiesResponse)
def capabilities(settings: Annotated[Settings, Depends(get_app_settings)]) -> CapabilitiesResponse:
    """Which gated features are enabled in this deployment. Public, no secrets."""
    return CapabilitiesResponse(version=__version__, capabilities=settings.enabled_capabilities())
