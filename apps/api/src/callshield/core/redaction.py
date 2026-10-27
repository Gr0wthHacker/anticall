"""Redaction for logs. Raw phone numbers must never reach general logs (PRD 8)."""

from __future__ import annotations

import re
from typing import Any

from callshield.core.phone import mask_phone

_PHONE_IN_TEXT = re.compile(r"\+?\d[\d\-\s().]{8,}\d")
_SENSITIVE_KEYS = ("password", "token", "secret", "authorization", "api_key", "jwt")
_PHONE_KEYS = frozenset(
    {"phone", "number", "e164", "from", "to", "caller_id", "displayed_caller_id"}
)


def redact_text(text: str) -> str:
    """Mask phone-number-like substrings inside free text."""

    def _mask(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 7:
            return match.group(0)
        return mask_phone("+" + digits)

    return _PHONE_IN_TEXT.sub(_mask, text)


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(s in lowered for s in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, str):
        if lowered in _PHONE_KEYS or lowered.endswith(("_phone", "_number")):
            return mask_phone(value)
        return redact_text(value)
    return redact_structure(value)


def redact_structure(data: Any) -> Any:
    """Recursively redact dict/list structures for logging."""
    if isinstance(data, dict):
        return {k: redact_value(str(k), v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [redact_text(v) if isinstance(v, str) else redact_structure(v) for v in data]
    return data
