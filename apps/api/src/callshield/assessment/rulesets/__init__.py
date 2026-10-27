"""Ruleset registry. Rulesets are versioned data modules; old versions stay loadable."""

from __future__ import annotations

from typing import Any

from callshield.assessment.rulesets.enf_2026_10_v1 import RULESET as _V1

RULESETS: dict[str, dict[str, Any]] = {_V1["ruleset_version"]: _V1}
CURRENT_RULESET_VERSION: str = _V1["ruleset_version"]


def get_ruleset(version: str | None = None) -> dict[str, Any]:
    key = version or CURRENT_RULESET_VERSION
    try:
        return RULESETS[key]
    except KeyError:
        raise KeyError(f"unknown ruleset_version: {key}") from None
