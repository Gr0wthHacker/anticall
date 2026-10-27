"""Versioned issue-spotting ruleset (PRD 19.9). DATA ONLY: no logic lives here.

Every issue type is a flag for counsel. Nothing here determines liability or any
monetary outcome, and nothing is legal advice. Re-review before each release
(docs/legal/regulatory-notes.md).
"""

# ruff: noqa: E501
from __future__ import annotations

from typing import Any

RULESET: dict[str, Any] = {
    "ruleset_version": "enf-issue-spotting-2026.10.27-v1",
    "reviewed": "2026-10-27",
    "counsel_reviewed": False,
    "disclaimer": (
        "Issue-spotting output only. This is not legal advice, not a legal determination, "
        "and not a prediction of any outcome. A flagged issue type means the recorded facts "
        "may be worth discussing with a licensed attorney; it does not mean any law applies "
        "or was broken. Call metadata and displayed caller IDs can be inaccurate or spoofed."
    ),
    # Parameters used by evaluators; counsel must confirm before relying on them.
    "parameters": {"registry_grace_days": 31},
    "checklist": {
        "line_control": "Subscriber / line control: the protected line is verified for this user",
        "call_dates_times": "Call dates and times are recorded for linked incidents",
        "call_type": "Call type is established (telemarketing, informational, debt, exempt)",
        "seller_identifiable": "The alleged seller or caller is identifiable (not only a displayed number)",
        "dnc_registry_history": "National DNC Registry registration date is known and precedes the calls",
        "entity_request_history": "An entity-specific do-not-call request is documented",
        "later_communications": "Contact occurred after the request, with timing recorded",
        "technology_type": "Technology used (live agent, prerecorded, artificial voice, autodialer)",
        "recording_authorization": "Recording / transcription authorization for any audio relied on",
        "jurisdiction": "Jurisdiction of caller and called party is known",
        "exemptions": "Possible exemptions or consent have been considered",
        "caller_id_evidence": "Evidence that the displayed caller ID is inaccurate",
    },
    "issue_types": {
        "DNC_REGISTRY": {
            "label": "Calls to a number on the National DNC Registry (issue spotting)",
            "requires": [
                "line_control",
                "call_dates_times",
                "call_type",
                "seller_identifiable",
                "dnc_registry_history",
                "exemptions",
            ],
            "counsel_if": ["call_type", "exemptions", "recording_authorization"],
            "counsel_always": False,
        },
        "DNC_ENTITY_SPECIFIC": {
            "label": "Contact after an entity-specific do-not-call request (issue spotting)",
            "requires": [
                "line_control",
                "call_dates_times",
                "call_type",
                "seller_identifiable",
                "entity_request_history",
                "later_communications",
                "exemptions",
            ],
            "counsel_if": ["call_type", "exemptions", "recording_authorization"],
            "counsel_always": False,
        },
        "AUTOMATED_OR_PRERECORDED_CALL": {
            "label": "Automated, prerecorded or artificial-voice call (issue spotting)",
            "requires": ["line_control", "call_dates_times", "technology_type", "exemptions"],
            "counsel_if": ["technology_type", "exemptions", "recording_authorization"],
            "counsel_always": False,
        },
        "TELEMARKETING_SALES_RULE": {
            "label": "Telemarketing Sales Rule coverage (issue spotting; coverage is fact-specific)",
            "requires": [
                "line_control",
                "call_dates_times",
                "call_type",
                "seller_identifiable",
                "exemptions",
            ],
            "counsel_if": ["recording_authorization"],
            "counsel_always": True,
        },
        "STATE_LAW": {
            "label": "State telemarketing or recording law (issue spotting)",
            "requires": ["line_control", "call_dates_times", "jurisdiction"],
            "counsel_if": ["recording_authorization"],
            "counsel_always": True,
        },
        "CALLER_ID_SPOOFING": {
            "label": "Inaccurate or spoofed caller ID (issue spotting)",
            "requires": ["line_control", "call_dates_times", "caller_id_evidence"],
            "counsel_if": ["recording_authorization"],
            "counsel_always": True,
        },
    },
    # Plain-language, non-legal explanations used in missing_requirements.
    "how_to_resolve": {
        "line_control": "Verify the protected line and link the case to it.",
        "call_dates_times": "Link at least one incident that has a recorded date and time.",
        "call_type": (
            "Confirm what kind of call this was (review the classification, or state it yourself)."
        ),
        "seller_identifiable": (
            "A displayed number alone cannot identify the seller; add a verified identity "
            "or corroboration."
        ),
        "dnc_registry_history": "State the date the number was registered on the DNC Registry.",
        "entity_request_history": (
            "Document a delivered do-not-call request, or state when you made one."
        ),
        "later_communications": "Link incidents that occurred after the request.",
        "technology_type": "State whether the call used a live agent or a recorded/artificial voice.",
        "recording_authorization": "Evaluate recording authorization before relying on audio.",
        "jurisdiction": "State the caller's and your own state or jurisdiction.",
        "exemptions": "State whether any consent, relationship or exemption might apply.",
        "caller_id_evidence": "Provide evidence the displayed caller ID was inaccurate.",
    },
}
