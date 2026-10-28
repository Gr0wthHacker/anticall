"""Profile lookup."""

from __future__ import annotations

from callshield.providers.base import ProviderProfile
from callshield.providers.profiles import GENERIC_SIP, PROFILES

DEFAULT_PROFILE_ID = GENERIC_SIP.id


def profile_ids() -> list[str]:
    return [p.id for p in PROFILES]


def get_profile(profile_id: str) -> ProviderProfile:
    for profile in PROFILES:
        if profile.id == profile_id:
            return profile
    raise KeyError(f"unknown provider profile: {profile_id}")
