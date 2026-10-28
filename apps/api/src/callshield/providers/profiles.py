"""Shipped provider profiles (data only). ``verified`` is False for every one.

Hosts are intentionally left empty where the provider assigns them per account/region.
Links were checked to resolve (HTTP 200) on 2026-10-27; their CONTENT was not reviewed.
"""

from __future__ import annotations

from callshield.providers.base import AttestationInfo, InputSpec, ProviderProfile

_COMMON_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("trunk_name", "Trunk name (letters, digits, - _)", default="trunk"),
    InputSpec("sip_host", "Provider SIP host or IP", "From your provider's portal."),
    InputSpec("sip_port", "Provider SIP port", required=False, default="5060"),
    InputSpec("did", "Your DID in E.164 (a number YOU own)", "Use your own number only."),
    InputSpec(
        "auth_mode",
        "Auth mode: registration or ip_auth",
        "Registration = we log in to the provider. ip_auth = provider sends from fixed IPs.",
        default="registration",
    ),
    InputSpec("username", "SIP username (registration mode)", required=False),
    InputSpec("password", "SIP password (registration mode)", secret=True, required=False),
    InputSpec(
        "ip_auth_match",
        "Provider source IPs/CIDRs for inbound (comma separated)",
        "Needed for ip_auth; recommended for registration. Copy from provider docs.",
        required=False,
    ),
)

GENERIC_SIP = ProviderProfile(
    id="generic_sip",
    display_name="Generic SIP trunk (manual)",
    description="Fully manual. You supply every value from your provider's documentation.",
    auth_style="manual",
    required_inputs=_COMMON_INPUTS,
    tos_notes="UNVERIFIED: ask your provider whether call screening and automated "
    "or AI-assisted calls are allowed on your account.",
)

TWILIO = ProviderProfile(
    id="twilio_elastic_sip",
    display_name="Twilio Elastic SIP Trunking (template)",
    auth_style="either",
    attestation=AttestationInfo("unknown", note="Check the provider's STIR/SHAKEN docs."),
    acceptable_use_url="https://www.twilio.com/en-us/legal/aup",
    docs_url="https://www.twilio.com/docs/sip-trunking",
    required_inputs=_COMMON_INPUTS,
)

TELNYX = ProviderProfile(
    id="telnyx",
    display_name="Telnyx SIP trunking (template)",
    auth_style="either",
    acceptable_use_url="https://telnyx.com/acceptable-use-policy",
    docs_url="https://developers.telnyx.com/docs/voice/sip-trunking",
    required_inputs=_COMMON_INPUTS,
)

BANDWIDTH = ProviderProfile(
    id="bandwidth",
    display_name="Bandwidth (template)",
    auth_style="ip_auth",
    acceptable_use_url="https://www.bandwidth.com/legal/acceptable-use-policy/",
    docs_url="https://dev.bandwidth.com/",
    required_inputs=_COMMON_INPUTS,
)

FLOWROUTE = ProviderProfile(
    id="flowroute",
    display_name="Flowroute (template)",
    auth_style="either",
    acceptable_use_url="",
    docs_url="https://www.flowroute.com/",
    tos_notes="UNVERIFIED: no acceptable-use link recorded; find it on the provider site.",
    required_inputs=_COMMON_INPUTS,
)

VOIPMS = ProviderProfile(
    id="voipms",
    display_name="VoIP.ms (template)",
    auth_style="registration",
    acceptable_use_url="https://voip.ms/tos",
    docs_url="https://wiki.voip.ms/",
    tos_notes="UNVERIFIED: links returned HTTP 403 to an automated check; open in a browser.",
    required_inputs=_COMMON_INPUTS,
)

ASTERISK_HARNESS = ProviderProfile(
    id="asterisk_test_harness",
    display_name="Local Asterisk test harness (synthetic)",
    description="Matches integrations/asterisk. No real carrier. Fictional numbers only.",
    sip_host="127.0.0.1",
    sip_port=5070,
    auth_style="ip_auth",
    attestation=AttestationInfo("none", note="The harness has no STIR/SHAKEN."),
    supports_outbound_a_attestation="no",
    tos_notes="Local only; no provider terms apply.",
    required_inputs=(
        InputSpec("trunk_name", "Trunk name", default="provider-sim"),
        InputSpec("did", "Test DID", default="+14155550102"),
        InputSpec("ip_auth_match", "Simulator source IP", required=False, default="127.0.0.1"),
    ),
    local_only=True,
)

PROFILES: tuple[ProviderProfile, ...] = (
    GENERIC_SIP,
    TWILIO,
    TELNYX,
    BANDWIDTH,
    FLOWROUTE,
    VOIPMS,
    ASTERISK_HARNESS,
)
