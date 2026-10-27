"""Case builder: automatic case construction, timeline, lifecycle and export (PRD 19).

Nothing here decides legal outcomes, places calls, or files complaints. Attribution
never rests on a displayed number alone, and no output claims admissibility.
"""

from __future__ import annotations

import callshield.db.models.enforce_ext  # noqa: F401  (register extension tables)
from callshield.casebuilder.attribution import (
    AttributionFeatures,
    evaluate_attribution,
    review_attribution,
)
from callshield.casebuilder.config import CaseBuilderConfig, config_for_tenant
from callshield.casebuilder.export import request_export, run_export
from callshield.casebuilder.imports import UserAccount, import_user_accounts
from callshield.casebuilder.lifecycle import transition_case
from callshield.casebuilder.pipeline import PipelineResult, run_incident_pipeline
from callshield.casebuilder.service import list_cases, open_case
from callshield.casebuilder.sufficiency import evidence_sufficiency
from callshield.casebuilder.timeline import TimelineEntry, build_timeline

__all__ = [
    "AttributionFeatures",
    "CaseBuilderConfig",
    "PipelineResult",
    "TimelineEntry",
    "UserAccount",
    "build_timeline",
    "config_for_tenant",
    "evaluate_attribution",
    "evidence_sufficiency",
    "import_user_accounts",
    "list_cases",
    "open_case",
    "request_export",
    "review_attribution",
    "run_export",
    "run_incident_pipeline",
    "transition_case",
]
