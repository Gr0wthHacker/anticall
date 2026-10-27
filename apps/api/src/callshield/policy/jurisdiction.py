"""Versioned jurisdiction policy table loaded from a JSON data file.

Every entry ships as UNKNOWN: this project asserts no legal conclusions. Counsel edits the
JSON (status, all_party_consent_possible, counsel_reviewed) per deployment. A missing or
corrupt file yields an all-UNKNOWN table (fail closed).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "jurisdictions.json"
UNKNOWN = "UNKNOWN"
PERMISSIVE_STATUS = "PERMITTED_WITH_CONSENT"


@dataclass(frozen=True)
class JurisdictionEntry:
    code: str
    status: str = UNKNOWN
    all_party_consent_possible: bool | None = None  # None = not assessed (treated as possible)
    counsel_reviewed: bool = False

    @property
    def permits_processing(self) -> bool:
        return self.status == PERMISSIVE_STATUS and self.counsel_reviewed


@dataclass(frozen=True)
class JurisdictionPolicy:
    version: str
    entries: dict[str, JurisdictionEntry]

    def lookup(self, code: str | None) -> JurisdictionEntry:
        key = (code or UNKNOWN).strip().upper()
        return self.entries.get(key) or JurisdictionEntry(code=key)

    def codes(self) -> list[str]:
        return sorted(self.entries)


def _entry(code: str, raw: dict[str, object]) -> JurisdictionEntry:
    all_party = raw.get("all_party_consent_possible")
    return JurisdictionEntry(
        code=code,
        status=str(raw.get("status", UNKNOWN)),
        all_party_consent_possible=all_party if isinstance(all_party, bool) else None,
        counsel_reviewed=raw.get("counsel_reviewed") is True,
    )


def load_jurisdiction_policy(path: Path | None = None) -> JurisdictionPolicy:
    try:
        data = json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
        raw_entries = data["jurisdictions"]
        entries = {code.upper(): _entry(code.upper(), raw) for code, raw in raw_entries.items()}
        return JurisdictionPolicy(version=str(data["version"]), entries=entries)
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return JurisdictionPolicy(version="fallback-all-unknown", entries={})


_override: list[JurisdictionPolicy] = []


@lru_cache
def _load_default() -> JurisdictionPolicy:
    configured = os.environ.get("CALLSHIELD_JURISDICTION_POLICY_FILE")
    return load_jurisdiction_policy(Path(configured) if configured else None)


def default_policy() -> JurisdictionPolicy:
    return _override[0] if _override else _load_default()


def set_policy_override(policy: JurisdictionPolicy | None) -> None:
    """Install a policy for tests / deployments that load counsel-reviewed data elsewhere."""
    _override.clear()
    if policy is not None:
        _override.append(policy)
