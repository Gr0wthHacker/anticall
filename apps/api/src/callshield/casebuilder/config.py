"""Case-builder configuration (ENF-003). Rules are versioned; a change bumps the version."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from callshield.domain.enums import ClassificationLabel

MATCH_RULESET_VERSION = "match-v1"


class CaseBuilderConfig(BaseModel):
    match_ruleset_version: str = MATCH_RULESET_VERSION
    # Default rule: open a CANDIDATE case on the first unwanted-classified call per
    # protected line + displayed-CLI / claimed-entity group.
    auto_open_case_on_first_unwanted_call: bool = True
    unwanted_labels: frozenset[str] = Field(
        default=frozenset(
            {
                ClassificationLabel.UNSOLICITED_SALES.value,
                ClassificationLabel.SPAM_SUSPECTED.value,
                ClassificationLabel.SCAM_SUSPECTED.value,
                ClassificationLabel.DEBT_COLLECTION.value,
            }
        )
    )
    script_similarity_threshold: float = 0.6
    timing_window_hours: int = 2
    timing_min_calls: int = 3
    min_independent_features: int = 2


DEFAULT_CONFIG = CaseBuilderConfig()

# Per-tenant overrides. In-process only; persisting them is a later (settings) concern.
TENANT_OVERRIDES: dict[uuid.UUID, CaseBuilderConfig] = {}


def config_for_tenant(tenant_id: uuid.UUID) -> CaseBuilderConfig:
    return TENANT_OVERRIDES.get(tenant_id, DEFAULT_CONFIG)
