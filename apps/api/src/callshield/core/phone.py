"""Phone helpers: E.164 normalization, log masking, reserved test numbers."""

from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_NANP_FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")  # NXX-555-0100..0199


class InvalidPhoneNumberError(ValueError):
    pass


def normalize_e164(raw: str, default_country_code: str = "1") -> str:
    """Normalize to E.164. 10-digit and 1+10-digit inputs assume NANP."""
    stripped = raw.strip()
    digits = re.sub(r"\D", "", stripped)
    nanp = default_country_code == "1"
    if stripped.startswith("+"):
        candidate = "+" + digits
    elif len(digits) == 10 and nanp:
        candidate = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1") and nanp:
        candidate = "+" + digits
    else:
        raise InvalidPhoneNumberError("cannot normalize phone number")
    if not _E164.match(candidate):
        raise InvalidPhoneNumberError("not a valid E.164 number")
    return candidate


def mask_phone(value: str | None) -> str:
    """Mask for logs, e.g. +14155550123 -> +1415***0123."""
    if not value:
        return "***"
    if len(value) <= 9:
        return value[:2] + "***"
    return f"{value[:5]}***{value[-4:]}"


def is_reserved_test_number(e164: str) -> bool:
    """True for fictional NANP numbers (NXX-555-0100 to 0199)."""
    return bool(_NANP_FICTIONAL.match(e164))
