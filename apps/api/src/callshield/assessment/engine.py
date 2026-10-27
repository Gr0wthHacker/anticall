"""Pure, deterministic evaluator for the versioned ruleset (PRD 19.9).

Outputs are restricted to AssessmentStatus values. There is no code path that emits a
proven-violation state or any monetary figure. No model is consulted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from callshield.assessment.facts import CaseFacts
from callshield.domain.enums import AssessmentStatus, OptOutStatus

ALLOWED_STATUSES: frozenset[str] = frozenset(s.value for s in AssessmentStatus)
_SALES_LABELS = frozenset({"UNSOLICITED_SALES"})
_REQUEST_MADE = frozenset(
    {
        OptOutStatus.DELIVERED.value,
        OptOutStatus.ACKNOWLEDGED.value,
        OptOutStatus.REFUSED.value,
        OptOutStatus.CESSATION_OBSERVED.value,
    }
)


class ItemState(StrEnum):
    SATISFIED = "SATISFIED"
    MISSING = "MISSING"
    CONTRADICTED = "CONTRADICTED"
    COUNSEL = "COUNSEL"


@dataclass(frozen=True)
class ItemResult:
    state: ItemState
    detail: str = ""


@dataclass
class IssueResult:
    issue_type: str
    status: AssessmentStatus
    supporting_event_ids: list[str] = field(default_factory=list)
    missing_requirements: list[dict[str, str]] = field(default_factory=list)
    contradictions: list[dict[str, str]] = field(default_factory=list)
    counsel_reasons: list[dict[str, str]] = field(default_factory=list)
    checklist: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "status": self.status.value,
            "supporting_event_ids": self.supporting_event_ids,
            "missing_requirements": self.missing_requirements,
            "contradictions": self.contradictions,
            "counsel_reasons": self.counsel_reasons,
            "checklist": self.checklist,
        }


Evaluator = Callable[[CaseFacts, dict[str, Any]], ItemResult]


def _ok(detail: str = "") -> ItemResult:
    return ItemResult(ItemState.SATISFIED, detail)


def _missing(detail: str) -> ItemResult:
    return ItemResult(ItemState.MISSING, detail)


def _line_control(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    line = facts.snapshot.line
    if line is None:
        return _missing("no protected line linked to the case")
    if line.verification_status != "VERIFIED":
        return _missing("protected line is not verified")
    return _ok()


def _call_dates(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    dated = [i for i in facts.snapshot.incidents if i.occurred_at is not None]
    if not dated:
        return _missing("no linked incident has a recorded date and time")
    return _ok(f"{len(dated)} dated incident(s)")


def _call_type(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    stated = facts.user.call_type
    if stated in ("INFORMATIONAL", "EXEMPT"):
        return ItemResult(ItemState.CONTRADICTED, f"user states call type {stated}")
    if stated == "DEBT_COLLECTION":
        return ItemResult(ItemState.COUNSEL, "debt-collection calls are governed by other rules")
    if stated == "TELEMARKETING" or facts.confirmed_labels & _SALES_LABELS:
        return _ok("telemarketing established by user statement or reviewed classification")
    if facts.inferred_labels & _SALES_LABELS:
        return _missing("only an unreviewed model-inferred label suggests sales; review it")
    return _missing("call type not established")


def _seller_identifiable(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    snap = facts.snapshot
    if snap.attribution_status == "CORROBORATED":
        return _ok("attribution corroborated")
    if any(c.verification_status == "VERIFIED" for c in snap.claims):
        return _ok("caller identity claim verified")
    return _missing("identity is unverified; a displayed number alone cannot identify a seller")


def _on_or_after(moment: datetime | None, day: date) -> bool:
    if moment is None:
        return False
    return moment.astimezone(UTC).date() >= day


def _registry_history(facts: CaseFacts, params: dict[str, Any]) -> ItemResult:
    registered = facts.user.dnc_registered_on
    if registered is None:
        return _missing("registration date not provided")
    cutoff = registered + timedelta(days=int(params["registry_grace_days"]))
    after = [i for i in facts.snapshot.incidents if _on_or_after(i.occurred_at, cutoff)]
    if not after:
        return ItemResult(ItemState.CONTRADICTED, "no linked call falls after the grace window")
    return _ok(f"{len(after)} call(s) after the registration grace window")


def _request_date(facts: CaseFacts) -> date | None:
    dates: list[date] = []
    if facts.user.entity_specific_request_on:
        dates.append(facts.user.entity_specific_request_on)
    for opt in facts.snapshot.opt_outs:
        if opt.status in _REQUEST_MADE:
            dates.append(opt.created_at.astimezone(UTC).date())
    return min(dates) if dates else None


def _entity_request(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    if _request_date(facts) is None:
        return _missing("no delivered do-not-call request is documented")
    return _ok("request documented (acknowledgment is not proof of suppression)")


def _later_communications(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    when = _request_date(facts)
    if when is None:
        return _missing("request date unknown")
    day_after = when + timedelta(days=1)  # same-day timing is ambiguous; be conservative
    later = [i for i in facts.snapshot.incidents if _on_or_after(i.occurred_at, day_after)]
    if not later:
        return ItemResult(ItemState.CONTRADICTED, "no linked contact after the request date")
    return _ok(f"{len(later)} contact(s) after the request date")


def _technology(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    tech = facts.user.technology
    if tech in ("PRERECORDED", "ARTIFICIAL_VOICE"):
        return _ok(f"user states {tech}")
    if tech == "AUTODIALER":
        return ItemResult(ItemState.COUNSEL, "autodialer definitions are contested; counsel")
    if tech == "LIVE_AGENT":
        return ItemResult(ItemState.CONTRADICTED, "user states a live agent placed the call")
    return _missing("technology type not stated")


def _recording_authorization(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    relies_on_audio = any(
        a.source_type.upper() in {"AUDIO_RECORDING", "AI_TRANSCRIPT"}
        or a.media_type.startswith("audio/")
        for a in facts.snapshot.artifacts
    )
    if not relies_on_audio:
        return _ok("no audio or transcript relied on")
    if facts.snapshot.recording_authorization == "PERMITTED":
        return _ok("recording authorization recorded as permitted")
    return ItemResult(ItemState.COUNSEL, "audio/transcript present without permitted authorization")


def _jurisdiction(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    if facts.user.caller_jurisdiction or facts.user.called_party_state:
        return _ok("jurisdiction statement provided")
    return _missing("caller and called-party jurisdiction not stated")


def _exemptions(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    status = facts.user.exemption_status
    if status == "NONE_KNOWN":
        return _ok("user knows of no consent or exemption")
    if status in ("POSSIBLE", "CLAIMED"):
        return ItemResult(ItemState.COUNSEL, f"possible exemption or consent: {status}")
    return _missing("exemptions and consent not considered")


def _caller_id_evidence(facts: CaseFacts, _p: dict[str, Any]) -> ItemResult:
    stated = facts.user.caller_id_inaccurate
    if stated is False:
        return ItemResult(ItemState.CONTRADICTED, "user states the caller ID was accurate")
    if stated and facts.attestation_metadata_seen:
        return _ok("user reports inaccuracy and attestation metadata is on file")
    return _missing("no evidence the displayed caller ID was inaccurate")


EVALUATORS: dict[str, Evaluator] = {
    "line_control": _line_control,
    "call_dates_times": _call_dates,
    "call_type": _call_type,
    "seller_identifiable": _seller_identifiable,
    "dnc_registry_history": _registry_history,
    "entity_request_history": _entity_request,
    "later_communications": _later_communications,
    "technology_type": _technology,
    "recording_authorization": _recording_authorization,
    "jurisdiction": _jurisdiction,
    "exemptions": _exemptions,
    "caller_id_evidence": _caller_id_evidence,
}


def _decide(
    spec: dict[str, Any],
    results: dict[str, ItemResult],
    result: IssueResult,
    ruleset: dict[str, Any],
) -> AssessmentStatus:
    for item in spec["requires"]:
        res = results[item]
        row = {"item": item, "label": ruleset["checklist"][item], "reason": res.detail}
        if res.state is ItemState.CONTRADICTED:
            result.contradictions.append(row)
        elif res.state is ItemState.MISSING:
            row["how_to_resolve"] = ruleset["how_to_resolve"][item]
            result.missing_requirements.append(row)
    for item in dict.fromkeys([*spec["requires"], *spec["counsel_if"]]):
        if results[item].state is ItemState.COUNSEL:
            result.counsel_reasons.append({"item": item, "reason": results[item].detail})
    if result.contradictions:
        return AssessmentStatus.NOT_SUPPORTED_BY_CURRENT_EVIDENCE
    if result.missing_requirements:
        return AssessmentStatus.INSUFFICIENT_INFORMATION
    if spec["counsel_always"] or result.counsel_reasons:
        return AssessmentStatus.REQUIRES_COUNSEL_REVIEW
    return AssessmentStatus.POSSIBLY_RELEVANT


def evaluate_case(facts: CaseFacts, ruleset: dict[str, Any]) -> list[IssueResult]:
    """Evaluate every issue type in the ruleset. Pure: same facts => same output."""
    params = ruleset["parameters"]
    results = {name: EVALUATORS[name](facts, params) for name in ruleset["checklist"]}
    out: list[IssueResult] = []
    for issue_type, spec in ruleset["issue_types"].items():
        result = IssueResult(issue_type, AssessmentStatus.INSUFFICIENT_INFORMATION)
        result.status = _decide(spec, results, result, ruleset)
        result.checklist = {
            item: results[item].state.value
            for item in dict.fromkeys([*spec["requires"], *spec["counsel_if"]])
        }
        result.supporting_event_ids = facts.support_ids
        out.append(result)
    return out
