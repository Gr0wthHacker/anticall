from __future__ import annotations

from pathlib import Path

import pytest

from callshield.casebuilder.attribution import (
    AttributionFeatures,
    evaluate_attribution,
    max_pairwise_similarity,
    repeat_contact_verified,
)
from callshield.casebuilder.config import DEFAULT_CONFIG, CaseBuilderConfig
from callshield.casebuilder.export_bundle import csv_safe, tenant_pseudonym
from callshield.casebuilder.matching import normalize_entity
from callshield.core.errors import UnprocessableError
from callshield.domain.enums import AttributionStatus, EvidenceLabel, EvidenceOrigin
from callshield.evidence.store import (
    LocalArtifactStore,
    ObjectExistsError,
    ObjectNotFoundError,
    S3ArtifactStore,
    UnsafeStorageKeyError,
    validate_storage_key,
)
from callshield.evidence.taxonomy import ALLOWED_LABELS, DEFAULT_LABEL, validate_taxonomy


@pytest.mark.parametrize(
    "key",
    ["../x", "a/../../x", "/abs", "a//b", "a/./b", "a\\b", ".hidden", "a/.secret", "", "a/b\x00c",
     "C:/x", "a/b:c"],
)  # fmt: skip
def test_unsafe_keys_rejected(key: str) -> None:
    with pytest.raises(UnsafeStorageKeyError):
        validate_storage_key(key)


def test_local_store_roundtrip_and_write_once(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.put("tenant/obj1", b"data")
    assert store.exists("tenant/obj1")
    assert store.get("tenant/obj1") == b"data"
    with pytest.raises(ObjectExistsError):
        store.put("tenant/obj1", b"other")
    assert store.get("tenant/obj1") == b"data"
    assert store.delete("tenant/obj1") is True
    assert store.delete("tenant/obj1") is False
    with pytest.raises(ObjectNotFoundError):
        store.get("tenant/obj1")


def test_local_store_blocks_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalArtifactStore(root)
    with pytest.raises(UnsafeStorageKeyError):
        store.put("../outside/evil", b"x")
    assert not (outside / "evil").exists()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(UnsafeStorageKeyError):
        store.put("link/evil", b"x")
    assert not (outside / "evil").exists()


def test_s3_store_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        S3ArtifactStore("bucket").put("a/b", b"x")


def test_every_origin_has_a_valid_default_label() -> None:
    for origin in EvidenceOrigin:
        assert DEFAULT_LABEL[origin] in ALLOWED_LABELS[origin]


def test_taxonomy_rejects_mismatched_labels() -> None:
    with pytest.raises(UnprocessableError):
        validate_taxonomy(EvidenceOrigin.USER_STATED, EvidenceLabel.OBSERVED.value, {})
    with pytest.raises(UnprocessableError):
        validate_taxonomy(EvidenceOrigin.CALLER_CLAIMED, EvidenceLabel.OBSERVED.value, {})
    validate_taxonomy(EvidenceOrigin.ORIGINAL, EvidenceLabel.OBSERVED.value, {})


def test_externally_verified_requires_scope() -> None:
    label = EvidenceLabel.EXTERNALLY_VERIFIED.value
    with pytest.raises(UnprocessableError):
        validate_taxonomy(EvidenceOrigin.EXTERNAL_DOCUMENTARY, label, {})
    validate_taxonomy(
        EvidenceOrigin.EXTERNAL_DOCUMENTARY, label, {"verification_scope": "sender email headers"}
    )


def test_same_displayed_number_never_corroborates() -> None:
    features = AttributionFeatures(same_displayed_cli=True)
    for reviewed in (False, True):
        decision = evaluate_attribution(features, DEFAULT_CONFIG, human_reviewed=reviewed)
        assert decision.status is AttributionStatus.CANDIDATE
        assert decision.independent_features == ()


def test_corroboration_needs_independent_features_and_human_review() -> None:
    features = AttributionFeatures(
        same_displayed_cli=True, claimed_entity_match=True, timing_pattern_match=True
    )
    pending = evaluate_attribution(features, DEFAULT_CONFIG)
    assert pending.status is AttributionStatus.CANDIDATE
    assert pending.requires_human_review is True
    reviewed = evaluate_attribution(features, DEFAULT_CONFIG, human_reviewed=True)
    assert reviewed.status is AttributionStatus.CORROBORATED


def test_one_independent_feature_is_not_enough_even_when_reviewed() -> None:
    features = AttributionFeatures(same_displayed_cli=True, claimed_entity_match=True)
    decision = evaluate_attribution(features, DEFAULT_CONFIG, human_reviewed=True)
    assert decision.status is AttributionStatus.CANDIDATE


def test_low_script_similarity_is_not_a_feature() -> None:
    features = AttributionFeatures(script_similarity=0.2, user_confirmed=True)
    assert evaluate_attribution(features, DEFAULT_CONFIG, True).status is not (
        AttributionStatus.CORROBORATED
    )


def test_repeat_contact_verified_only_when_corroborated() -> None:
    assert repeat_contact_verified("CANDIDATE") is False
    assert repeat_contact_verified("UNKNOWN") is False
    assert repeat_contact_verified("CORROBORATED") is True


def test_script_similarity() -> None:
    same = max_pairwise_similarity(
        ["we can lower your loan rate today", "We can lower your loan rate today!"]
    )
    assert same is not None and same > 0.9
    assert max_pairwise_similarity(["only one"]) is None
    differ = max_pairwise_similarity(["lower your loan rate", "your package is delayed"])
    assert differ is not None and differ < 0.3


def test_entity_normalization() -> None:
    assert normalize_entity("Acme Lending, LLC.") == normalize_entity("ACME lending inc")
    assert normalize_entity(None) == ""


def test_csv_formula_injection_neutralized() -> None:
    assert csv_safe("=SUM(A1)") == "'=SUM(A1)"
    assert csv_safe("+cmd|calc") == "'+cmd|calc"
    assert csv_safe("-2+3") == "-2+3" or csv_safe("-2+3").startswith("'")
    assert csv_safe("+14155550177") == "+14155550177"  # phone numbers are data, not formulas
    assert csv_safe("normal") == "normal"
    assert csv_safe(None) == ""


def test_tenant_pseudonym_is_stable_and_opaque() -> None:
    import uuid

    tid = uuid.uuid4()
    assert tenant_pseudonym(tid) == tenant_pseudonym(tid)
    assert str(tid) not in tenant_pseudonym(tid)
    assert tenant_pseudonym(tid) != tenant_pseudonym(uuid.uuid4())


def test_config_is_versioned_and_defaults_open_candidate_cases() -> None:
    assert DEFAULT_CONFIG.match_ruleset_version
    assert DEFAULT_CONFIG.auto_open_case_on_first_unwanted_call is True
    assert "UNSOLICITED_SALES" in DEFAULT_CONFIG.unwanted_labels
    assert "TRUSTED" not in DEFAULT_CONFIG.unwanted_labels
    assert CaseBuilderConfig(match_ruleset_version="match-v2").match_ruleset_version == "match-v2"
