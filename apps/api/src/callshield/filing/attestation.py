"""Filing attestation (PRD 21.3). Mirrors docs/legal/filing-attestation-v1.json.

A test asserts this copy stays identical to the JSON in docs/legal so legal text edits
cannot silently drift. The text is a DRAFT that still needs counsel review.
"""

from __future__ import annotations

from typing import Any

ATTESTATION_VERSION = "filing-attestation-v1"

ATTESTATION_V1: dict[str, Any] = {
    "id": "filing-attestation",
    "version": "v1",
    "status": "DRAFT_NOT_COUNSEL_REVIEWED",
    "source": "PRD_v3 section 21.3",
    "notice": "Draft. Not legal advice. Requires counsel and jurisdiction-specific adaptation.",
    "per_submission": True,
    "record_fields": [
        "draft_id",
        "user_id",
        "approved_payload_digest",
        "attestation_version",
        "authorized_at_utc",
    ],
    "must_display_before_accept": [
        "complaint_text",
        "recipient",
        "submission_channel",
        "attachments",
    ],
    "statements": [
        {
            "id": "reviewed_exact_content",
            "required": True,
            "text": "I have reviewed the exact complaint text, recipient, and attachments and "
            "believe the statements I am submitting are accurate to the best of my knowledge.",
        },
        {
            "id": "evidence_types_differ",
            "required": True,
            "text": "I understand that observed call metadata, caller statements, and AI "
            "inferences are different types of evidence; that the identity of the responsible "
            "caller or business may be unverified.",
        },
        {
            "id": "no_outcome_guarantee",
            "required": True,
            "text": "I understand that filing a complaint does not establish a legal violation "
            "or guarantee investigation, compensation, or removal from a calling list.",
        },
        {
            "id": "authorize_transmission",
            "required": True,
            "text": "I authorize transmission of the selected information to the named "
            "recipient using the described submission channel.",
        },
        {
            "id": "entitled_to_disclose",
            "required": True,
            "text": "I understand I am responsible for confirming that I am entitled to "
            "disclose the included material, and that recordings or transcripts may be "
            "subject to consent laws.",
        },
    ],
}

SUPPORTED_VERSIONS: frozenset[str] = frozenset({ATTESTATION_VERSION})


def required_statement_ids() -> list[str]:
    return [s["id"] for s in ATTESTATION_V1["statements"] if s["required"]]


def required_display_items() -> list[str]:
    return list(ATTESTATION_V1["must_display_before_accept"])
