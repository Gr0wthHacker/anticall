"""HMAC-SHA256 webhook signing: timestamp tolerance, nonce, constant-time compare.

Signed message: b"<timestamp>.<nonce>." + raw body. Headers (case-insensitive):
X-CallShield-Timestamp (unix seconds), X-CallShield-Nonce, X-CallShield-Signature
(`v1=<hex>`). Each delivery attempt must be signed with a fresh nonce; the same nonce
is rejected as a replay (see defend.ingest).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping

from callshield.core.clock import utcnow
from callshield.telephony.base import WebhookVerificationError

TIMESTAMP_HEADER = "x-callshield-timestamp"
NONCE_HEADER = "x-callshield-nonce"
SIGNATURE_HEADER = "x-callshield-signature"
_NONCE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
_SIG_PREFIX = "v1="


def compute_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = f"{timestamp}.{nonce}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return _SIG_PREFIX + digest


def sign_headers(secret: str, timestamp: int, nonce: str, body: bytes) -> dict[str, str]:
    ts = str(timestamp)
    return {
        "X-CallShield-Timestamp": ts,
        "X-CallShield-Nonce": nonce,
        "X-CallShield-Signature": compute_signature(secret, ts, nonce, body),
    }


def _lowered(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def verify_signed_request(
    headers: Mapping[str, str], body: bytes, secret: str, tolerance_seconds: int
) -> str:
    """Return the nonce when valid; raise WebhookVerificationError otherwise."""
    if not secret:
        raise WebhookVerificationError("NO_SECRET")
    h = _lowered(headers)
    ts, nonce, sig = h.get(TIMESTAMP_HEADER), h.get(NONCE_HEADER), h.get(SIGNATURE_HEADER)
    if not ts or not nonce or not sig:
        raise WebhookVerificationError("MISSING_HEADERS")
    if not _NONCE.match(nonce) or not ts.isdigit() or len(ts) > 12:
        raise WebhookVerificationError("MALFORMED_HEADERS")
    expected = compute_signature(secret, ts, nonce, body)
    if not hmac.compare_digest(expected.encode(), sig.encode()):
        raise WebhookVerificationError("BAD_SIGNATURE")
    skew = abs(int(utcnow().timestamp()) - int(ts))
    if skew > tolerance_seconds:
        raise WebhookVerificationError("STALE_TIMESTAMP")
    return nonce
