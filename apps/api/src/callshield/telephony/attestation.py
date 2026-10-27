"""Parse a network STIR/SHAKEN attestation level. Network attestation only, never a verdict
about the caller. Anything missing, stripped or unparseable is "NONE"."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

LEVELS = frozenset({"A", "B", "C"})
NONE = "NONE"
_VERSTAT = re.compile(r"TN-Validation-Passed-([ABC])\b", re.IGNORECASE)


def _level(value: object) -> str | None:
    if isinstance(value, str) and value.strip().upper() in LEVELS:
        return value.strip().upper()
    return None


def _passport_attest(identity: str) -> str | None:
    """SIP Identity header: '<jwt>;info=<...>;alg=ES256;ppt=shaken' -> payload 'attest'."""
    token = identity.split(";", 1)[0].strip().strip("<>")
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims: Any = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError):
        return None
    return _level(claims.get("attest")) if isinstance(claims, dict) else None


def parse_attestation_level(attestation: Mapping[str, Any] | None) -> str:
    data = attestation or {}
    identity = data.get("identity")
    if isinstance(identity, str) and (found := _passport_attest(identity)):
        return found
    if found := _level(data.get("attest")):
        return found
    verstat = data.get("verstat")
    if isinstance(verstat, str) and (m := _VERSTAT.search(verstat)):
        return m.group(1).upper()
    return NONE
