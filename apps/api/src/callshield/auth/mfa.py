"""MFA / OIDC extension point.

TODO(security): NOT IMPLEMENTED. PRD 7.1/11.2 require MFA for privileged users and
OIDC support. This module only defines the interface so later work can plug in a
vetted library (e.g. TOTP via pyotp, OIDC via authlib). Nothing here fakes a
second factor: the default provider refuses to verify anything.
"""

from __future__ import annotations

from typing import Protocol

from callshield.auth.tokens import Principal


class SecondFactorProvider(Protocol):
    def enroll(self, principal: Principal) -> dict[str, str]:
        """Begin enrollment; return provider-specific provisioning data."""
        ...

    def verify(self, principal: Principal, code: str) -> bool: ...


class OidcProvider(Protocol):
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> Principal: ...


class NotImplementedSecondFactor:
    def enroll(self, principal: Principal) -> dict[str, str]:
        raise NotImplementedError("MFA is not implemented yet")

    def verify(self, principal: Principal, code: str) -> bool:
        return False  # fail closed; never accept a code


def get_second_factor_provider() -> SecondFactorProvider:
    return NotImplementedSecondFactor()
