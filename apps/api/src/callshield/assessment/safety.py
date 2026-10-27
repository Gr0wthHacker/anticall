"""Output safety: assessments and drafts never assert proof of a violation or a money figure."""

from __future__ import annotations

import re
from typing import Any

from callshield.core.errors import UnprocessableError
from callshield.core.hashing import canonical_json

FORBIDDEN_OUTPUT = re.compile(
    r"VIOLATION_PROVEN|\bdamages?\b|\bliable\b|\bguilty\b|\bstatutory\b|\btreble\b|\$\s?\d",
    re.IGNORECASE,
)


def find_forbidden(value: Any) -> list[str]:
    """Return forbidden fragments found anywhere in a JSON-able structure."""
    text = value if isinstance(value, str) else canonical_json(value)
    return sorted({m.group(0) for m in FORBIDDEN_OUTPUT.finditer(text)})


def assert_clean(value: Any, where: str) -> None:
    """Fail loudly if generated output contains a forbidden assertion."""
    found = find_forbidden(value)
    if found:
        raise RuntimeError(f"forbidden content in {where}: {found}")


def reject_forbidden_input(value: str, field_name: str) -> None:
    """User-supplied text is kept out of generated filings/assessments as well."""
    if find_forbidden(value):
        raise UnprocessableError(
            f"{field_name} must not assert proof of a violation or monetary amounts",
            code="FORBIDDEN_CONTENT",
            details={"field": field_name},
        )
