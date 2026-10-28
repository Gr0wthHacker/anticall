"""GET /v1/system/doctor: the same check engine as `callshield doctor`, admin-only.

Instance-wide (not tenant data). Details never contain secrets or file paths.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Engine

from callshield.auth.deps import get_app_settings, require_role
from callshield.auth.tokens import Principal
from callshield.configreg.gates import RISKY_FEATURES
from callshield.core.config import Settings
from callshield.doctor.checks import risky_enabled
from callshield.doctor.deps import check_engine, default_deps
from callshield.doctor.engine import run_checks
from callshield.doctor.loader import config_from_app_settings
from callshield.doctor.model import DoctorDeps
from callshield.domain.enums import Role

router = APIRouter(tags=["system"])

DOCS = {
    "quickstart": "docs/quickstart.md",
    "connect_trunk": "docs/deployment/connect-your-sip-trunk.md",
    "configuration_reference": "docs/deployment/configuration-reference.md",
    "going_live": "docs/deployment/going-live-checklist.md",
}


class DoctorCheckOut(BaseModel):
    id: str
    title: str
    status: str
    detail: str
    fix: str


class GatedFeatureOut(BaseModel):
    key: str
    env_var: str
    title: str
    enabled: bool
    gates: str
    warning: str


class DoctorOut(BaseModel):
    ok: bool
    counts: dict[str, int]
    checks: list[DoctorCheckOut]
    gated_features: list[GatedFeatureOut]
    docs: dict[str, str]


def get_doctor_deps(request: Request) -> DoctorDeps:
    """Overridable in tests. Uses the running app's own engine for the database check."""
    deps = default_deps()
    engine: Engine = request.app.state.engine
    deps.check_db = lambda _url: check_engine(engine)
    return deps


@router.get("/v1/system/doctor", response_model=DoctorOut)
def system_doctor(
    _principal: Annotated[Principal, Depends(require_role(Role.HOUSEHOLD_ADMIN, Role.OPERATOR))],
    settings: Annotated[Settings, Depends(get_app_settings)],
    deps: Annotated[DoctorDeps, Depends(get_doctor_deps)],
) -> DoctorOut:
    cfg = config_from_app_settings(settings)
    report = run_checks(cfg, deps)
    on = set(risky_enabled(cfg))
    return DoctorOut(
        ok=report.ok,
        counts=report.counts,
        checks=[DoctorCheckOut(**r.as_dict()) for r in report.results],
        gated_features=[
            GatedFeatureOut(
                key=f.key,
                env_var=f.env_var,
                title=f.title,
                enabled=f.env_var in on,
                gates=f.gates,
                warning=f.warning,
            )
            for f in RISKY_FEATURES
        ],
        docs=DOCS,
    )
