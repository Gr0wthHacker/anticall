"""Deterministic routing engine, urgency detection, processing permission module."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from callshield.core.config import Settings
from callshield.domain.enums import ClassificationLabel, ProcessingMode, RoutingAction
from callshield.policy.config import LineRoutingConfig, parse_routing_config
from callshield.policy.jurisdiction import (
    DATA_FILE,
    JurisdictionEntry,
    JurisdictionPolicy,
    load_jurisdiction_policy,
)
from callshield.policy.processing import evaluate_processing
from callshield.policy.routing import (
    CallContext,
    ContactEntry,
    RoutingDecision,
    RuleEntry,
    RuleMatcher,
    decide_route,
    fallback_decision,
    matcher_matches,
    ring_block_reason,
)
from callshield.policy.urgency import detect_urgency

LINE = "+14155550100"
USER = "+14155550101"
CALLER = "+14155550123"
FRIEND = "+14155550111"
SPAMMER = "+14155550122"


def cfg(**kwargs: Any) -> LineRoutingConfig:
    return LineRoutingConfig(user_number=USER, **kwargs)


def ctx(caller: str | None = CALLER, **kwargs: Any) -> CallContext:
    return CallContext(caller=caller, line_e164=LINE, **kwargs)


def rule(priority: int, matcher: dict[str, Any], action: str) -> RuleEntry:
    return RuleEntry(uuid.uuid4(), priority, matcher, action)


# ------------------------------------------------------------------ routing


def test_trusted_contact_rings_user() -> None:
    d = decide_route(cfg(), [ContactEntry(FRIEND, "TRUSTED")], [], ctx(FRIEND), True)
    assert (d.action, d.op, d.target, d.reason) == (
        RoutingAction.TRANSFER,
        "transfer",
        USER,
        "TRUSTED_CONTACT",
    )


@pytest.mark.parametrize("kind", ["TRUSTED", "PERMITTED", "OVERRIDE"])
def test_all_ringing_contact_kinds_reach_user(kind: str) -> None:
    d = decide_route(cfg(), [ContactEntry(FRIEND, kind)], [], ctx(FRIEND), True)
    assert d.op == "transfer"


def test_trusted_contact_never_blocked_by_rule_or_unwanted_listing() -> None:
    rules = [
        rule(1, {"caller_exact": [FRIEND]}, "REJECT"),
        rule(2, {"caller_prefix": ["+1415"]}, "REJECT"),
    ]
    contacts = [ContactEntry(FRIEND, "KNOWN_UNWANTED"), ContactEntry(FRIEND, "TRUSTED")]
    d = decide_route(cfg(), contacts, rules, ctx(FRIEND), False)
    assert d.op == "transfer" and d.reason == "TRUSTED_CONTACT"


@pytest.mark.parametrize("action,op", [("VOICEMAIL", "voicemail"), ("REJECT", "hangup")])
def test_known_unwanted_follows_config(action: str, op: str) -> None:
    d = decide_route(
        cfg(known_unwanted_action=action),
        [ContactEntry(SPAMMER, "KNOWN_UNWANTED")],
        [],
        ctx(SPAMMER),
        True,
    )
    assert d.op == op and d.reason == "KNOWN_UNWANTED"


def test_known_unwanted_default_is_voicemail_not_hangup() -> None:
    d = decide_route(cfg(), [ContactEntry(SPAMMER, "KNOWN_UNWANTED")], [], ctx(SPAMMER), True)
    assert d.op == "voicemail"


def test_rules_evaluate_in_priority_order() -> None:
    rules = [
        rule(50, {"caller_prefix": ["+1415"]}, "REJECT"),
        rule(10, {"caller_exact": [CALLER]}, "VOICEMAIL"),
    ]
    d = decide_route(cfg(), [], rules, ctx(), True)
    assert d.op == "voicemail" and d.rule_id == rules[1].id


def test_rule_can_allow_ring_and_screen() -> None:
    allow = decide_route(cfg(), [], [rule(1, {"caller_exact": [CALLER]}, "ALLOW")], ctx(), True)
    assert allow.op == "transfer" and allow.action == RoutingAction.ALLOW
    screen = decide_route(
        cfg(unknown_action="VOICEMAIL"), [], [rule(1, {"anonymous": False}, "SCREEN")], ctx(), True
    )
    assert screen.screens


def test_rule_screen_without_permission_degrades_to_provider_routing() -> None:
    d = decide_route(cfg(), [], [rule(1, {"anonymous": False}, "SCREEN")], ctx(), False)
    assert not d.screens and d.op == "voicemail" and d.reason == "SCREENING_NOT_PERMITTED"


def test_invalid_matcher_and_unknown_action_never_match() -> None:
    rules = [
        rule(1, {"bogus": True}, "REJECT"),
        rule(2, {}, "REJECT"),
        rule(3, {"anonymous": False}, "NUKE"),
    ]
    d = decide_route(cfg(unknown_action="VOICEMAIL"), [], rules, ctx(), True)
    assert d.reason == "UNKNOWN_CALLER_VOICEMAIL"


def test_matcher_criteria_are_anded() -> None:
    both = {"caller_prefix": ["+1415"], "attestation_in": ["A"]}
    assert not matcher_matches(both, ctx(attestation={"attest": "B"}))
    assert matcher_matches(both, ctx(attestation={"attest": "a"}))
    assert matcher_matches({"hop_count_gte": 2}, ctx(hop_count=2))
    assert matcher_matches({"attestation_in": ["NONE"]}, ctx())


def test_anonymous_caller_matches_only_anonymous_rules_and_no_contacts() -> None:
    anon = ctx(None)
    assert matcher_matches({"anonymous": True}, anon)
    assert not matcher_matches({"caller_prefix": ["+1"]}, anon)
    d = decide_route(cfg(), [ContactEntry(FRIEND, "TRUSTED")], [], anon, True)
    assert d.screens


def test_unknown_caller_screens_when_permitted() -> None:
    d = decide_route(cfg(), [], [], ctx(), True)
    assert d.screens and d.op == "answer"


def test_unknown_caller_falls_back_to_provider_routing_when_not_permitted() -> None:
    assert decide_route(cfg(), [], [], ctx(), False).op == "voicemail"
    ring = decide_route(cfg(unscreened_unknown_action="RING"), [], [], ctx(), False)
    assert ring.op == "transfer" and ring.target == USER


@pytest.mark.parametrize("action,op", [("VOICEMAIL", "voicemail"), ("RING", "transfer")])
def test_unknown_action_config(action: str, op: str) -> None:
    assert decide_route(cfg(unknown_action=action), [], [], ctx(), True).op == op


def test_routing_is_deterministic() -> None:
    args = (
        cfg(),
        [ContactEntry(FRIEND, "TRUSTED")],
        [rule(1, {"anonymous": True}, "REJECT")],
        ctx(),
        True,
    )
    assert decide_route(*args) == decide_route(*args)


# ------------------------------------------------------------------ loop prevention


def test_no_user_number_means_voicemail_not_a_crash() -> None:
    d = decide_route(LineRoutingConfig(), [ContactEntry(FRIEND, "TRUSTED")], [], ctx(FRIEND), True)
    assert d.op == "voicemail" and d.reason == "NO_USER_NUMBER"


@pytest.mark.parametrize(
    "config,context,reason",
    [
        (LineRoutingConfig(user_number=LINE), ctx(), "SELF_LOOP"),
        (cfg(), ctx(diversions=(USER,)), "LOOP_DETECTED"),
        (cfg(), ctx(USER), "LOOP_DETECTED"),
        (cfg(max_hops=2), ctx(hop_count=2), "MAX_HOPS_EXCEEDED"),
    ],
)
def test_loop_prevention_never_forwards(
    config: LineRoutingConfig, context: CallContext, reason: str
) -> None:
    assert ring_block_reason(config, context) == reason
    caller = context.caller or FRIEND
    contacts = [ContactEntry(caller, "TRUSTED")]
    call = CallContext(caller, LINE, context.hop_count, context.diversions)
    d = decide_route(config, contacts, [], call, True)
    assert d.op == "voicemail" and d.reason == reason


def test_hops_below_limit_still_forward() -> None:
    assert ring_block_reason(cfg(max_hops=3), ctx(hop_count=2)) is None


# ------------------------------------------------------------------ fallback


@pytest.mark.parametrize(
    "setting,op", [("TRANSFER", "transfer"), ("VOICEMAIL", "voicemail"), ("HANGUP", "hangup")]
)
def test_fallback_follows_config(setting: str, op: str) -> None:
    d = fallback_decision(cfg(ai_failure_fallback=setting), ctx(), "X")
    assert d.action == RoutingAction.FALLBACK and d.op == op


def test_fallback_transfer_degrades_to_voicemail_on_loop() -> None:
    d = fallback_decision(cfg(), ctx(diversions=(USER,)), "X")
    assert d.op == "voicemail"


# ------------------------------------------------------------------ config + matcher models


def test_matcher_model_rejects_empty_and_bad_values() -> None:
    for bad in ({}, {"caller_prefix": ["415"]}, {"caller_exact": ["abc"]}, {"extra": 1}):
        with pytest.raises(ValidationError):
            RuleMatcher(**bad)
    assert RuleMatcher(caller_exact=["(415) 555-0123"]).caller_exact == [CALLER]


def test_config_rejects_unknown_keys_and_bad_numbers() -> None:
    with pytest.raises(ValidationError):
        LineRoutingConfig(nope=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        LineRoutingConfig(user_number="12")


def test_parse_routing_config_is_lenient_and_keeps_valid_fields() -> None:
    parsed = parse_routing_config(
        {"user_number": USER, "unknown_action": "BOGUS", "legacy_key": 1, "max_hops": 5}
    )
    assert parsed.user_number == USER and parsed.max_hops == 5
    assert parsed.unknown_action == "SCREEN"
    assert parse_routing_config(None) == LineRoutingConfig()


# ------------------------------------------------------------------ urgency (D-012)


@pytest.mark.parametrize(
    "text,label",
    [
        ("I'm calling from the hospital about your husband", ClassificationLabel.URGENT_MEDICAL),
        ("This is the ER, we need you to come in", ClassificationLabel.URGENT_MEDICAL),
        ("This is Lincoln Elementary School, your son is sick", ClassificationLabel.URGENT_SCHOOL),
        ("daycare calling, can you pick her up", ClassificationLabel.URGENT_SCHOOL),
        ("there was an accident, call me back", ClassificationLabel.URGENT_FAMILY),
        ("your mother asked me to call you", ClassificationLabel.URGENT_FAMILY),
        ("this is a police officer, it is an emergency", ClassificationLabel.EMERGENCY),
        ("Call 911", ClassificationLabel.EMERGENCY),
    ],
)
def test_urgency_labels(text: str, label: ClassificationLabel) -> None:
    result = detect_urgency(text)
    assert result.label == label and result.is_urgent and result.matched


def test_emergency_outranks_other_urgent_labels() -> None:
    assert (
        detect_urgency("school nurse, emergency, ambulance on the way").label
        == ClassificationLabel.EMERGENCY
    )


@pytest.mark.parametrize(
    "text", ["", None, "I'd like to talk about solar panels", "special offer today"]
)
def test_non_urgent_text(text: str | None) -> None:
    assert not detect_urgency(text).is_urgent


# ------------------------------------------------------------------ processing permission


def permitted(
    code: str = "US-TEST", all_party: bool | None = False, reviewed: bool = True
) -> JurisdictionPolicy:
    return JurisdictionPolicy(
        "t", {code: JurisdictionEntry(code, "PERMITTED_WITH_CONSENT", all_party, reviewed)}
    )


@dataclass
class Consent:
    processing_mode: str
    authorized: bool = True
    revoked_at: Any = None
    legal_basis_config: dict[str, Any] = field(default_factory=dict)


def live_settings(**kwargs: Any) -> Settings:
    return Settings(db_url="sqlite://", live_stt_enabled=True, **kwargs)


def decide(consents: list[Consent], jurisdiction: str | None = "US-TEST", **kwargs: Any) -> Any:
    settings = kwargs.pop("settings", live_settings())
    line_config = kwargs.pop("line_config", {})
    return evaluate_processing(
        settings, line_config, consents, jurisdiction, kwargs.pop("policy", permitted())
    )


def test_default_is_deny_everything() -> None:
    d = evaluate_processing(Settings(db_url="sqlite://"), {}, [], None)
    assert not (
        d.allow_live_stt or d.allow_recording or d.allow_transcript_persist or d.allow_remote_stt
    )
    assert d.mode == ProcessingMode.METADATA_ONLY
    assert "LIVE_STT_DISABLED_BY_DEPLOYMENT" in d.reasons and "JURISDICTION_UNKNOWN" in d.reasons


def test_deployment_flag_off_denies_even_with_consent_and_permitted_jurisdiction() -> None:
    d = decide([Consent("LIVE_STT_TRANSIENT")], settings=Settings(db_url="sqlite://"))
    assert not d.allow_live_stt and "LIVE_STT_DISABLED_BY_DEPLOYMENT" in d.reasons


def test_unknown_jurisdiction_denies() -> None:
    d = decide([Consent("LIVE_STT_TRANSIENT")], jurisdiction="US-ZZ")
    assert not d.allow_live_stt and "JURISDICTION_UNKNOWN" in d.reasons
    assert not decide([Consent("LIVE_STT_TRANSIENT")], jurisdiction=None).allow_live_stt


def test_not_counsel_reviewed_denies() -> None:
    d = decide([Consent("LIVE_STT_TRANSIENT")], policy=permitted(reviewed=False))
    assert not d.allow_live_stt and "JURISDICTION_NOT_PERMITTED" in d.reasons


def test_no_consent_denies() -> None:
    d = decide([])
    assert not d.allow_live_stt and "NO_CONSENT" in d.reasons


def test_live_transient_allowed_with_consent_but_nothing_persists() -> None:
    d = decide([Consent("LIVE_STT_TRANSIENT")])
    assert d.allow_live_stt and d.mode == ProcessingMode.LIVE_STT_TRANSIENT
    assert not d.allow_recording and not d.allow_transcript_persist and not d.allow_remote_stt


def test_persistence_requires_its_own_consent() -> None:
    assert decide([Consent("TRANSCRIPT_RETAINED")]).allow_transcript_persist
    d = decide([Consent("RECORDING_RETAINED")])
    assert (
        d.allow_recording
        and d.allow_transcript_persist
        and d.mode == ProcessingMode.RECORDING_RETAINED
    )
    assert not decide([Consent("LIVE_STT_TRANSIENT")]).allow_transcript_persist


def test_all_party_possible_requires_confirmed_basis() -> None:
    policy = permitted(all_party=True)
    assert (
        "ALL_PARTY_BASIS_NOT_CONFIRMED"
        in decide([Consent("LIVE_STT_TRANSIENT")], policy=policy).reasons
    )
    ok = decide(
        [Consent("LIVE_STT_TRANSIENT", legal_basis_config={"all_party_basis_confirmed": True})],
        policy=policy,
    )
    assert ok.allow_live_stt
    unassessed = decide([Consent("LIVE_STT_TRANSIENT")], policy=permitted(all_party=None))
    assert not unassessed.allow_live_stt


def test_revocation_clears_prior_grant() -> None:
    d = decide([Consent("LIVE_STT_TRANSIENT"), Consent("LIVE_STT_TRANSIENT", authorized=False)])
    assert not d.allow_live_stt
    regrant = decide(
        [Consent("LIVE_STT_TRANSIENT", authorized=False), Consent("LIVE_STT_TRANSIENT")]
    )
    assert regrant.allow_live_stt


def test_revoked_at_marks_grant_inactive() -> None:
    assert not decide([Consent("LIVE_STT_TRANSIENT", revoked_at="2026-01-01")]).allow_live_stt


def test_remote_stt_needs_line_opt_in_and_consent_flag() -> None:
    flag = {"remote_stt_ok": True}
    assert not decide([Consent("LIVE_STT_TRANSIENT", legal_basis_config=flag)]).allow_remote_stt
    assert not decide(
        [Consent("LIVE_STT_TRANSIENT")], line_config={"allow_remote_stt": True}
    ).allow_remote_stt
    both = decide(
        [Consent("LIVE_STT_TRANSIENT", legal_basis_config=flag)],
        line_config={"allow_remote_stt": True},
    )
    assert both.allow_remote_stt


def test_remote_stt_never_allowed_when_live_denied() -> None:
    d = decide([], line_config={"allow_remote_stt": True})
    assert not d.allow_remote_stt


def test_unknown_mode_strings_are_ignored() -> None:
    assert not decide([Consent("TELEPATHY")]).allow_live_stt


def test_decision_dict_is_json_serializable() -> None:
    json.dumps(decide([Consent("LIVE_STT_TRANSIENT")]).as_dict())


# ------------------------------------------------------------------ jurisdiction data file


def test_shipped_jurisdiction_data_is_all_unknown_and_unasserted() -> None:
    policy = load_jurisdiction_policy()
    assert len(policy.entries) >= 52
    for entry in policy.entries.values():
        assert entry.status == "UNKNOWN"
        assert entry.all_party_consent_possible is None
        assert not entry.counsel_reviewed
        assert not entry.permits_processing
    assert policy.lookup("US-CA").status == "UNKNOWN"
    assert policy.lookup("does-not-exist").status == "UNKNOWN"


def test_shipped_data_denies_even_with_everything_else_in_place() -> None:
    d = evaluate_processing(live_settings(), {}, [Consent("LIVE_STT_TRANSIENT")], "US-CA")
    assert not d.allow_live_stt and "JURISDICTION_UNKNOWN" in d.reasons


def test_missing_or_corrupt_jurisdiction_file_fails_closed(tmp_path: Path) -> None:
    assert load_jurisdiction_policy(tmp_path / "missing.json").lookup("US-CA").status == "UNKNOWN"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_jurisdiction_policy(bad).version == "fallback-all-unknown"
    assert DATA_FILE.exists()


def test_jurisdiction_file_can_be_reviewed_by_counsel(tmp_path: Path) -> None:
    path = tmp_path / "j.json"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "jurisdictions": {
                    "us-xx": {
                        "status": "PERMITTED_WITH_CONSENT",
                        "all_party_consent_possible": False,
                        "counsel_reviewed": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    policy = load_jurisdiction_policy(path)
    assert policy.lookup("US-XX").permits_processing
    d = evaluate_processing(live_settings(), {}, [Consent("LIVE_STT_TRANSIENT")], "US-XX", policy)
    assert d.allow_live_stt and d.policy_version == "v1"


def test_routing_decision_is_immutable() -> None:
    d = RoutingDecision(RoutingAction.TRANSFER, "transfer", "X")
    with pytest.raises(AttributeError):
        d.op = "hangup"  # type: ignore[misc]
