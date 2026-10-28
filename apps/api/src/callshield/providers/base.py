"""Provider profile data model.

A profile is a TEMPLATE of sensible defaults for one SIP provider. Nothing in a profile is a
statement of fact about that provider unless ``verified`` is True (no shipped profile is).
Always confirm hosts, auth style, codecs, attestation and terms against the provider's own
documentation before connecting real traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthStyle = Literal["registration", "ip_auth", "either", "manual"]
OutboundAttestation = Literal["unknown", "documented", "no"]
AttestationSource = Literal["identity_header", "verstat", "vendor_header", "none", "unknown"]
DidSource = Literal["request_uri", "to_header", "custom_header", "unknown"]
Transport = Literal["udp", "tcp", "tls"]

UNVERIFIED_BANNER = (
    "UNVERIFIED TEMPLATE: provider-specific values below are defaults, not facts. "
    "Confirm every value against the provider's current documentation."
)


@dataclass(frozen=True)
class InputSpec:
    """One value the user must (or may) supply for this profile."""

    key: str
    label: str
    help: str = ""
    secret: bool = False
    required: bool = True
    default: str = ""


@dataclass(frozen=True)
class AttestationInfo:
    """Where (if anywhere) the provider exposes STIR/SHAKEN attestation on inbound calls."""

    source: AttestationSource = "unknown"
    header_name: str = ""
    note: str = ""


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    display_name: str
    description: str = ""
    sip_host: str = ""
    sip_port: int = 5060
    transport: Transport = "udp"
    auth_style: AuthStyle = "manual"
    codecs: tuple[str, ...] = ("ulaw", "alaw")
    did_source: DidSource = "request_uri"
    did_header: str = ""
    attestation: AttestationInfo = AttestationInfo()
    supports_outbound_a_attestation: OutboundAttestation = "unknown"
    tos_notes: str = "UNVERIFIED: read the provider's acceptable-use terms yourself."
    acceptable_use_url: str = ""
    docs_url: str = ""
    required_inputs: tuple[InputSpec, ...] = ()
    verified: bool = False
    local_only: bool = False

    def input_keys(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in self.required_inputs)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "auth_style": self.auth_style,
            "verified": self.verified,
            "supports_outbound_a_attestation": self.supports_outbound_a_attestation,
            "acceptable_use_url": self.acceptable_use_url,
            "docs_url": self.docs_url,
        }
