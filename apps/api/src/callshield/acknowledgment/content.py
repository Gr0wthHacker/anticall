"""Acknowledgment (PRD Appendix B). Mirrors docs/legal/acknowledgment-v1.json.

A test asserts this copy stays identical to the JSON in docs/legal so legal text edits cannot
silently drift. The text is a DRAFT that still needs counsel review.
"""

from __future__ import annotations

from typing import Any

ACKNOWLEDGMENT_VERSION = "acknowledgment-v1"

ACKNOWLEDGMENT_V1: dict[str, Any] = {
    "id": "acknowledgment",
    "version": "v1",
    "status": "DRAFT_NOT_COUNSEL_REVIEWED",
    "source": "PRD_v3 Appendix B",
    "notice": "Draft. Not legal advice. Use only after counsel review and enforceability check.",
    "preamble": "Before activating telephony features, I acknowledge that:",
    "all_required": True,
    "default_checked": False,
    "record_fields": ["accepted_at_utc", "acknowledgment_version", "text_sha256", "operator_id"],
    "items": [
        {
            "id": "line_control",
            "required": True,
            "text": "I control or am authorized to manage the telephone lines connected to "
            "this installation.",
        },
        {
            "id": "legal_compliance",
            "required": True,
            "text": "I am responsible for obtaining necessary permissions and complying with "
            "applicable law and carrier/provider terms.",
        },
        {
            "id": "caller_id_unreliable",
            "required": True,
            "text": "Displayed caller ID may be false or spoofed and does not, by itself, "
            "identify a responsible organization.",
        },
        {
            "id": "ai_may_err",
            "required": True,
            "text": "AI-generated assessments may be incorrect; I must review consequential "
            "outcomes.",
        },
        {
            "id": "recording_consent",
            "required": True,
            "text": "Recording, transcription, and live AI processing may require additional "
            "disclosures and consents.",
        },
        {
            "id": "no_suppression_guarantee",
            "required": True,
            "text": "A removal request or verbal acknowledgment does not guarantee suppression "
            "of further calling.",
        },
        {
            "id": "no_harassment",
            "required": True,
            "text": "Repeated contacts made to harass, intimidate, or disrupt a destination "
            "are not authorized by the project's intended workflows.",
        },
        {
            "id": "experimental",
            "required": True,
            "text": "I understand that the project is experimental and can lose or mishandle "
            "calls if misconfigured.",
        },
        {
            "id": "documents_reviewed",
            "required": True,
            "text": "I have reviewed the license, disclaimer, privacy notice, and any "
            "applicable service terms.",
        },
    ],
}
