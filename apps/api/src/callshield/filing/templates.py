"""Versioned complaint templates, required-field validation (ENF-013) and the
completeness check (ENF-012: explicit missing sources, never a legal-validity score).

Text is composed deterministically from stored facts. No model writes or decides
anything here. Templates never assert a legal conclusion or a monetary amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from callshield.assessment.safety import reject_forbidden_input
from callshield.core.errors import UnprocessableError
from callshield.filing.snapshot import CaseSnapshot, IncidentFact

MIN_NARRATIVE_CHARS = 20
MAX_NARRATIVE_CHARS = 4000
MAX_LISTED_CALLS = 50
LIMITATIONS: tuple[str, ...] = (
    "Displayed caller numbers can be inaccurate or spoofed; the caller's identity is unverified.",
    "Call metadata, caller statements and any automated inference are different kinds of evidence.",
    "This report is not a legal conclusion and does not ask for any particular outcome.",
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: str  # "enum" | "text"
    allowed: tuple[str, ...] = ()
    max_len: int = 200
    required: bool = False


@dataclass(frozen=True)
class TemplateSpec:
    key: str  # also the stored template_version
    agency: str
    title: str
    user_fields: tuple[FieldSpec, ...]
    narrative_required: bool
    intro: str


_NARRATIVE = FieldSpec("narrative", "Your description", "text", max_len=MAX_NARRATIVE_CHARS)
_CONTACT = FieldSpec(
    "contact_kind",
    "How the caller reached you",
    "enum",
    ("LIVE_CALL", "PRERECORDED_OR_ARTIFICIAL_VOICE", "TEXT_MESSAGE", "UNKNOWN"),
    required=True,
)

TEMPLATES: dict[str, TemplateSpec] = {
    "fcc_unwanted_call_v1": TemplateSpec(
        key="fcc_unwanted_call_v1",
        agency="FCC",
        title="Unwanted call report for the FCC Consumer Complaint Center",
        user_fields=(_CONTACT,),
        narrative_required=True,
        intro="I am reporting unwanted calls I received.",
    ),
    "ftc_dnc_v1": TemplateSpec(
        key="ftc_dnc_v1",
        agency="FTC",
        title="Unwanted call report for DoNotCall.gov",
        user_fields=(
            _CONTACT,
            FieldSpec(
                "dnc_registration_status",
                "Is the number on the National Do Not Call Registry?",
                "enum",
                ("REGISTERED", "NOT_REGISTERED", "UNKNOWN"),
                required=True,
            ),
        ),
        narrative_required=False,
        intro="I am reporting unwanted calls I received.",
    ),
    "ftc_fraud_v1": TemplateSpec(
        key="ftc_fraud_v1",
        agency="FTC",
        title="Suspected scam or fraud report for ReportFraud.ftc.gov",
        user_fields=(
            FieldSpec(
                "payment_made",
                "Did you send money or give payment details to the caller?",
                "enum",
                ("YES", "NO"),
                required=True,
            ),
        ),
        narrative_required=True,
        intro="I am reporting calls I believe may be part of a scam.",
    ),
    "carrier_report_v1": TemplateSpec(
        key="carrier_report_v1",
        agency="CARRIER",
        title="Unwanted call report for my phone carrier",
        user_fields=(
            FieldSpec("carrier_name", "Your carrier's name", "text", max_len=100, required=True),
        ),
        narrative_required=True,
        intro="I am reporting unwanted calls I received on my line.",
    ),
}


def get_template(key: str | None) -> TemplateSpec | None:
    return TEMPLATES.get(key) if key else None


def validate_user_fields(spec: TemplateSpec, fields: dict[str, Any]) -> dict[str, str]:
    """Reject unknown keys and bad values; return cleaned string fields (422 on error)."""
    known = {f.name: f for f in spec.user_fields} | {"narrative": _NARRATIVE}
    unknown = sorted(set(fields) - set(known))
    if unknown:
        raise UnprocessableError(
            f"unknown fields for {spec.key}: {', '.join(unknown)}",
            code="UNKNOWN_FIELDS",
            details={"unknown": unknown, "allowed": sorted(known)},
        )
    cleaned: dict[str, str] = {}
    for name, raw in fields.items():
        field_spec = known[name]
        value = str(raw).strip() if raw is not None else ""
        if not value:
            continue
        if field_spec.kind == "enum" and value not in field_spec.allowed:
            raise UnprocessableError(
                f"{name} must be one of {', '.join(field_spec.allowed)}",
                code="INVALID_FIELD",
                details={"field": name, "allowed": list(field_spec.allowed)},
            )
        if len(value) > field_spec.max_len:
            raise UnprocessableError(
                f"{name} exceeds {field_spec.max_len} characters",
                code="INVALID_FIELD",
                details={"field": name},
            )
        reject_forbidden_input(value, name)
        cleaned[name] = value
    return cleaned


def _check(name: str, ok: bool, reason: str) -> dict[str, str]:
    return {"field": name, "status": "PRESENT" if ok else "MISSING", "reason": "" if ok else reason}


def _snapshot_checks(snapshot: CaseSnapshot, calls: list[IncidentFact]) -> list[dict[str, str]]:
    line = snapshot.line
    return [
        _check(
            "complainant_line",
            line is not None and line.verification_status == "VERIFIED",
            "the protected line must exist and be verified",
        ),
        _check(
            "calls",
            any(c.occurred_at is not None for c in calls),
            "at least one selected incident with a recorded date and time",
        ),
        _check(
            "caller_number",
            any(c.displayed_caller_id for c in calls),
            "at least one selected incident with an observed displayed number",
        ),
    ]


def check_completeness(
    spec: TemplateSpec,
    snapshot: CaseSnapshot,
    calls: list[IncidentFact],
    fields: dict[str, str],
) -> dict[str, Any]:
    """List every required item and what is missing. No percentage, no validity score."""
    checks = _snapshot_checks(snapshot, calls)
    narrative = fields.get("narrative", "")
    if spec.narrative_required:
        checks.append(
            _check(
                "narrative",
                len(narrative) >= MIN_NARRATIVE_CHARS,
                f"describe what happened in at least {MIN_NARRATIVE_CHARS} characters",
            )
        )
    for spec_field in spec.user_fields:
        if spec_field.required:
            checks.append(_check(spec_field.name, spec_field.name in fields, spec_field.label))
    missing = [c for c in checks if c["status"] == "MISSING"]
    warnings = []
    if snapshot.attribution_status != "CORROBORATED":
        warnings.append("caller identity is unverified; the report says so")
    if any(not c.confirmed for c in calls):
        warnings.append("some calls are suspected, not confirmed; they are labeled as such")
    return {
        "template": spec.key,
        "complete": not missing,
        "checked": checks,
        "missing": missing,
        "warnings": warnings,
        "note": "Checklist of missing sources only; not a measure of legal validity.",
    }


def _call_line(call: IncidentFact) -> str:
    when = call.occurred_at.isoformat() if call.occurred_at else "time not recorded"
    number = call.displayed_caller_id or "no number recorded"
    state = "confirmed by me" if call.confirmed else "suspected, not yet confirmed"
    return f"- {when} UTC, displayed number {number} ({call.evidence_label}; {state})"


def build_complaint_text(
    spec: TemplateSpec,
    line_e164: str,
    calls: list[IncidentFact],
    claimed_names: list[str],
    opt_out_count: int,
    fields: dict[str, str],
) -> str:
    """Deterministic complaint text. Shown verbatim to the user before approval."""
    lines = [spec.title, "", spec.intro, f"Number that received the calls: {line_e164}"]
    lines.append(f"Calls reported ({len(calls)}):")
    lines.extend(_call_line(c) for c in calls[:MAX_LISTED_CALLS])
    if len(calls) > MAX_LISTED_CALLS:
        lines.append(f"- and {len(calls) - MAX_LISTED_CALLS} more calls in the attached call list")
    lines.append(LIMITATIONS[0])
    if claimed_names:
        names = "; ".join(sorted(set(claimed_names)))
        lines.append(f"Name(s) stated by the caller (claimed, not verified): {names}")
    if opt_out_count:
        lines.append(
            f"Do-not-call requests I documented: {opt_out_count} "
            "(an acknowledgment by the caller does not mean the calls stopped)."
        )
    for spec_field in spec.user_fields:
        if spec_field.name in fields:
            lines.append(f"{spec_field.label} {fields[spec_field.name]}")
    if fields.get("narrative"):
        lines.extend(["", "My description:", fields["narrative"]])
    lines.extend(["", LIMITATIONS[1], LIMITATIONS[2]])
    return "\n".join(lines)
