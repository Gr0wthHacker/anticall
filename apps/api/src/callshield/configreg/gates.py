"""Risky features: plain-language warning, the release gate each relates to, env switch.

Gate names refer to docs/legal/release-gates.md (G1-G5 are the callback counsel gates).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskyFeature:
    key: str
    env_var: str
    title: str
    warning: str
    gates: str
    needs_callback_confirmation: bool = False


RISKY_FEATURES: tuple[RiskyFeature, ...] = (
    RiskyFeature(
        "ari",
        "CALLSHIELD_ASTERISK_ENABLED",
        "Asterisk ARI telephony control",
        "Connects CallShield to a live phone system that answers real calls. Misconfiguration "
        "can drop or mishandle calls and expose your SIP trunk to toll fraud.",
        "G5 provider terms",
    ),
    RiskyFeature(
        "live_stt",
        "CALLSHIELD_LIVE_STT_ENABLED",
        "Live speech-to-text on calls",
        "Processes caller audio live. Some jurisdictions treat that as recording or "
        "interception and need consent.",
        "G3 listen window / all-party consent; counsel",
    ),
    RiskyFeature(
        "outbound",
        "CALLSHIELD_OUTBOUND_ENABLED",
        "Outbound calling",
        "Allows the system to place any outbound call. Automated or repeated calls can "
        "violate telemarketing, robocall and harassment laws and your carrier's terms.",
        "G1, G2, G4, G5; counsel",
    ),
    RiskyFeature(
        "ai_outbound",
        "CALLSHIELD_AI_OUTBOUND_ENABLED",
        "AI voice on outbound calls",
        "An automated voice speaks on calls you place. Many states regulate automated and "
        "prerecorded calls, and the AI can be wrong.",
        "G1, G4, G5; counsel",
    ),
    RiskyFeature(
        "community",
        "CALLSHIELD_COMMUNITY_ENABLED",
        "Community signal sharing",
        "Shares signals about numbers with other users. Not built beyond tables; privacy "
        "and defamation review needed.",
        "counsel (privacy)",
    ),
    RiskyFeature(
        "filing_submission",
        "CALLSHIELD_FILING_SUBMISSION_ENABLED",
        "Programmatic agency filing",
        "Submits complaints to an agency channel on your behalf. A wrong or false filing is "
        "your responsibility. Manual handoff stays available when this is off.",
        "counsel (filing attestation)",
    ),
    RiskyFeature(
        "callback",
        "CALLSHIELD_CALLBACK_CALLBACK_ENABLED",
        "Automated callback to a number that called you",
        "Places one automated, disclosed call to a number that recently called you, only "
        "when the inbound call was STIR/SHAKEN A-attested. Whether this is lawful is OPEN "
        "(G1-G5). Do not enable it on real numbers without counsel sign-off.",
        "G1, G2, G3, G4, G5; counsel",
        needs_callback_confirmation=True,
    ),
    RiskyFeature(
        "callback_recording",
        "CALLSHIELD_CALLBACK_OUTBOUND_RECORDING",
        "Recording of callback calls",
        "Stores audio of the callback. All-party-consent states require disclosure first.",
        "G3; counsel",
    ),
    RiskyFeature(
        "llm",
        "CALLSHIELD_LLM_ENABLED",
        "LLM classification/summaries",
        "Sends call text to the LLM endpoint you configure. A remote endpoint means call "
        "content leaves your machine. The model only labels; it never decides or dials.",
        "G5 vendor terms; privacy",
    ),
)

CALLBACK_CONFIRMATION_SENTENCE = (
    "I understand callbacks are legally unresolved and I take responsibility for enabling them"
)


def risky_by_key() -> dict[str, RiskyFeature]:
    return {feature.key: feature for feature in RISKY_FEATURES}
