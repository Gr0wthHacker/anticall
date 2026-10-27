"""Evidence taxonomy (PRD 19.4, ENF-002): origin vs trust label, kept separate and visible."""

from __future__ import annotations

from typing import Any

from callshield.core.errors import UnprocessableError
from callshield.domain.enums import EvidenceLabel as L
from callshield.domain.enums import EvidenceOrigin as O

ALLOWED_LABELS: dict[O, frozenset[L]] = {
    O.ORIGINAL: frozenset({L.OBSERVED, L.USER_ATTESTED}),
    O.DERIVED: frozenset(L),  # derivatives inherit the label of their source
    O.USER_STATED: frozenset({L.USER_ATTESTED}),
    O.CALLER_CLAIMED: frozenset({L.CLAIMED}),
    O.INFERRED: frozenset({L.MODEL_INFERRED}),
    O.EXTERNAL_DOCUMENTARY: frozenset({L.EXTERNALLY_VERIFIED, L.USER_ATTESTED}),
}

DEFAULT_LABEL: dict[O, L] = {
    O.ORIGINAL: L.OBSERVED,
    O.DERIVED: L.MODEL_INFERRED,
    O.USER_STATED: L.USER_ATTESTED,
    O.CALLER_CLAIMED: L.CLAIMED,
    O.INFERRED: L.MODEL_INFERRED,
    O.EXTERNAL_DOCUMENTARY: L.USER_ATTESTED,
}

LABEL_DESCRIPTIONS: dict[L, str] = {
    L.OBSERVED: "recorded by the software or carrier; observed number is not a verified caller",
    L.CLAIMED: "stated by the caller; not verified",
    L.MODEL_INFERRED: "produced by a model or rule; may be wrong",
    L.USER_ATTESTED: "reported by the user; not independently verified",
    L.EXTERNALLY_VERIFIED: "backed by an external document; see verification scope",
}


def validate_taxonomy(origin: EvidenceOriginLike, label: str, provenance: dict[str, Any]) -> None:
    """Reject inconsistent origin/label pairs; EXTERNALLY_VERIFIED must state its scope."""
    origin_enum = O(origin)
    label_enum = L(label)
    if label_enum not in ALLOWED_LABELS[origin_enum]:
        raise UnprocessableError(
            f"label {label_enum.value} is not valid for origin {origin_enum.value}",
            code="TAXONOMY_MISMATCH",
        )
    if label_enum is L.EXTERNALLY_VERIFIED and not provenance.get("verification_scope"):
        raise UnprocessableError(
            "EXTERNALLY_VERIFIED evidence must state provenance.verification_scope",
            code="VERIFICATION_SCOPE_REQUIRED",
        )


EvidenceOriginLike = O | str
