"""STIR/SHAKEN attestation level parsing (network attestation only)."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from callshield.telephony.attestation import parse_attestation_level


def passport(
    claims: dict[str, Any], suffix: str = ";info=<https://cert.example.test>;ppt=shaken"
) -> str:
    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'ES256', 'ppt': 'shaken'})}.{enc(claims)}.c2ln{suffix}"


@pytest.mark.parametrize("level", ["A", "B", "C"])
def test_identity_header_attest_claim(level: str) -> None:
    assert parse_attestation_level({"identity": passport({"attest": level})}) == level


def test_identity_wins_over_other_fields() -> None:
    data = {
        "identity": passport({"attest": "B"}),
        "attest": "A",
        "verstat": "TN-Validation-Passed-C",
    }
    assert parse_attestation_level(data) == "B"


@pytest.mark.parametrize("raw,expected", [("a", "A"), (" C ", "C"), ("B", "B")])
def test_plain_attest_value(raw: str, expected: str) -> None:
    assert parse_attestation_level({"attest": raw}) == expected


@pytest.mark.parametrize(
    "verstat,expected",
    [
        ("TN-Validation-Passed-A", "A"),
        ("tn-validation-passed-c", "C"),
        ("TN-Validation-Passed", "NONE"),
        ("TN-Validation-Failed", "NONE"),
    ],
)
def test_verstat(verstat: str, expected: str) -> None:
    assert parse_attestation_level({"verstat": verstat}) == expected


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"attest": "Z"},
        {"attest": 5},
        {"identity": "garbage"},
        {"identity": "a.b.c"},
        {"identity": passport({"attest": "Q"})},
        {"identity": passport({})},
        {"identity": 7},
        {"identity": "x.%%%.z"},
    ],
)
def test_missing_stripped_or_unparseable_is_none(data: Any) -> None:
    assert parse_attestation_level(data) == "NONE"
