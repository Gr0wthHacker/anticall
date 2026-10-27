"""Approved, versioned conversation templates (PRD 9.3). The agent can speak ONLY these.

Free text from a model is never spoken. Changing any text requires bumping
TEMPLATE_VERSION; the version is recorded on every attempt (policy/template version).
"""

from __future__ import annotations

from dataclasses import dataclass

TEMPLATE_VERSION = "9.3-2026.1"
POLICY_VERSION = "resolve-policy-2026.1"


@dataclass(frozen=True)
class Template:
    id: str
    text: str


OUTBOUND_OPT_OUT = "OUTBOUND_OPT_OUT"
CALLBACK_FOLLOWUP = "CALLBACK_FOLLOWUP"
ACK_UNCLEAR = "ACK_UNCLEAR"
END_GOODBYE = "END_GOODBYE"
DISCLOSE_AUTOMATED = "DISCLOSE_AUTOMATED"

_TEMPLATES = (
    Template(
        OUTBOUND_OPT_OUT,
        "I'm an automated assistant calling on behalf of the person associated with this "
        "telephone number. They request that your organization stop solicitation calls to "
        "that number and add it to your internal do-not-call list. Can you confirm you've "
        "received the request?",
    ),
    Template(
        CALLBACK_FOLLOWUP,
        "Is there a specific compliance channel where this request can be delivered in "
        "writing, or someone who can record it now?",
    ),
    Template(
        ACK_UNCLEAR,
        "I understand you may not be able to confirm processing today. I will record that "
        "the request was communicated, but confirmation is outstanding.",
    ),
    Template(END_GOODBYE, "Understood. Goodbye."),
    Template(
        DISCLOSE_AUTOMATED,
        "Yes, I'm an automated assistant calling on behalf of the person associated with "
        "this telephone number.",
    ),
)

APPROVED_TEMPLATES: dict[str, Template] = {t.id: t for t in _TEMPLATES}


def template_text(template_id: str) -> str | None:
    template = APPROVED_TEMPLATES.get(template_id)
    return template.text if template else None


def versioned_id(template_id: str) -> str:
    """Stored as request_text_version on opt_out_events."""
    return f"{template_id}@{TEMPLATE_VERSION}"
