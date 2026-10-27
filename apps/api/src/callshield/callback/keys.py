"""Privacy-minimal destination keys.

The instance-wide caps and blocklist must count the SAME destination across tenants, so they
cannot be keyed by tenant data. Storing raw numbers in a cross-tenant table would let any
tenant's data reveal which numbers other tenants called. Instead we store only
HMAC-SHA256(server_secret, e164): without the server-side secret the table cannot be
reversed or probed by dictionary attack on the (small) phone-number space, and it contains
no tenant, user or call identifiers. Treat the secret as permanent: rotating it orphans every
existing global counter and blocklist entry (the keys can no longer be recomputed).
"""

from __future__ import annotations

import hashlib
import hmac

from callshield.callback.config import DEV_HMAC_SECRET
from callshield.core.errors import PolicyBlockedError
from callshield.core.phone import InvalidPhoneNumberError, normalize_e164


class DestinationKeyer:
    def __init__(self, secret: str, *, allow_dev_default: bool) -> None:
        if secret == DEV_HMAC_SECRET and not allow_dev_default:
            raise PolicyBlockedError(
                "callback HMAC secret is not configured", code="CALLBACK_SECRET_MISSING"
            )
        self._secret = secret.encode()

    def key(self, e164: str) -> str:
        try:
            canonical = normalize_e164(e164)
        except InvalidPhoneNumberError as exc:
            raise PolicyBlockedError(
                "destination is not a valid number", code="DESTINATION_INVALID"
            ) from exc
        return hmac.new(self._secret, canonical.encode(), hashlib.sha256).hexdigest()
