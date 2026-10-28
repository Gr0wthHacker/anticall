"""Provider profiles and renderers: golden files, validation, secret handling."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from callshield.providers.base import ProviderProfile
from callshield.providers.profiles import PROFILES
from callshield.providers.registry import get_profile, profile_ids
from callshield.providers.render_compose import render_compose_override
from callshield.providers.render_pjsip import RenderInputError, render_pjsip
from callshield.providers.secret_io import write_private_file

GOLDEN = Path(__file__).resolve().parent.parent / "golden"

CASES: dict[str, tuple[str, dict[str, str]]] = {
    "pjsip_generic_registration.conf": (
        "generic_sip",
        {
            "trunk_name": "mytrunk",
            "sip_host": "sip.example.test",
            "username": "acct123",
            "password": "test-password-not-real",
            "auth_mode": "registration",
            "include_transport": "true",
            "external_ip": "203.0.113.10",
        },
    ),
    "pjsip_telnyx_ip_auth.conf": (
        "telnyx",
        {
            "trunk_name": "telnyx-in",
            "sip_host": "sip.example.test",
            "auth_mode": "ip_auth",
            "ip_auth_match": "192.0.2.0/24,198.51.100.7",
        },
    ),
    "pjsip_asterisk_harness.conf": ("asterisk_test_harness", {"auth_mode": "ip_auth"}),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_pjsip_matches_golden(name: str) -> None:
    profile_id, inputs = CASES[name]
    rendered = render_pjsip(get_profile(profile_id), inputs)
    expected = (GOLDEN / name).read_text(encoding="utf-8")
    assert rendered == expected


def test_no_shipped_profile_claims_to_be_verified() -> None:
    assert all(not p.verified for p in PROFILES)
    assert all(p.supports_outbound_a_attestation != "documented" for p in PROFILES)


def test_profile_ids_unique_and_generic_is_default() -> None:
    ids = profile_ids()
    assert len(ids) == len(set(ids))
    assert ids[0] == "generic_sip"
    assert {"twilio_elastic_sip", "telnyx", "bandwidth", "flowroute", "voipms"} <= set(ids)


def test_real_provider_profiles_point_to_terms_or_say_unverified() -> None:
    for p in PROFILES:
        if p.local_only:
            continue
        assert p.acceptable_use_url.startswith("https://") or "UNVERIFIED" in p.tos_notes


def test_unverified_banner_in_output() -> None:
    text = render_pjsip(
        get_profile("telnyx"), {"sip_host": "sip.example.test", "auth_mode": "ip_auth"}
    )
    assert "UNVERIFIED" in text


@pytest.mark.parametrize(
    "bad",
    [
        {"trunk_name": "bad name"},
        {"trunk_name": "x\ny"},
        {"sip_host": "host\nallow=all"},
        {"password": "a;b", "auth_mode": "registration"},
        {"sip_port": "99999", "auth_mode": "registration"},
        {"auth_mode": "magic"},
    ],
)
def test_injection_and_bad_values_rejected(bad: dict[str, str]) -> None:
    inputs = {"sip_host": "sip.example.test", **bad}
    with pytest.raises(RenderInputError):
        render_pjsip(get_profile("generic_sip"), inputs)


def test_missing_credentials_become_placeholders_not_blank() -> None:
    text = render_pjsip(get_profile("generic_sip"), {"sip_host": "sip.example.test"})
    assert "CHANGE_ME" in text


def test_every_profile_renders_with_minimal_inputs() -> None:
    for p in PROFILES:
        assert isinstance(p, ProviderProfile)
        assert "type=endpoint" in render_pjsip(p, {"sip_host": "sip.example.test"})


def test_write_private_file_is_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "secret.conf"
    write_private_file(target, "password=x\n")
    assert target.read_text() == "password=x\n"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_compose_override_binds_nothing_public_by_default() -> None:
    text = render_compose_override("./pjsip.callshield.conf")
    assert "ports" not in text
    assert "0.0.0.0" not in render_compose_override("./p.conf", publish_sip=True)
    with pytest.raises(ValueError):
        render_compose_override("a\nb")
