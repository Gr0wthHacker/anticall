from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from callshield.core.clock import FrozenClock, set_clock
from callshield.core.config import Settings
from callshield.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PolicyBlockedError,
    UnprocessableError,
)
from callshield.core.hashing import sha256_hex
from callshield.db.models import Case, EvidenceArtifact, LedgerEvent, Tenant
from callshield.db.models.enforce_ext import DataSubjectRequest
from callshield.db.models.tenancy import User
from callshield.db.session import make_session_factory
from callshield.domain.enums import (
    AuthorizationState,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
)
from callshield.evidence import (
    ArtifactInput,
    RedactionSpec,
    build_data_export,
    delete_artifacts,
    expire_sweep,
    get_artifact,
    ingest_artifact,
    redact_artifact,
    request_artifact_deletion,
    require_sharing,
    revise_transcript,
    schedule_expire_sweep,
    set_authorization,
    set_legal_hold,
    transcript_versions,
    verify_integrity,
    withdraw_processing,
)
from callshield.evidence.integrity import MISMATCH, MISSING, OK
from callshield.evidence.store import LocalArtifactStore
from callshield.worker.queue import enqueue
from callshield.worker.runner import load_handler_modules, run_one
from tests.enforce_support import seed_line

pytest_plugins = ("tests.enforce_support",)

PERMITTED = AuthorizationState.PERMITTED
ACTOR = "user:test"


def _tenant(session: Session, name: str = "T") -> Tenant:
    tenant = Tenant(name=name)
    session.add(tenant)
    session.flush()
    return tenant


def _user(session: Session, tenant: Tenant) -> uuid.UUID:
    user = User(
        tenant_id=tenant.id, email=f"{uuid.uuid4().hex}@example.test", role="HOUSEHOLD_ADMIN"
    )
    session.add(user)
    session.flush()
    return user.id


def _case(session: Session, tenant: Tenant) -> Case:
    line = seed_line(session, tenant.id)
    case = Case(tenant_id=tenant.id, protected_line_id=line.id, title="t")
    session.add(case)
    session.flush()
    return case


def _doc(
    case: Case | None = None, data: bytes = b"hello world", **overrides: object
) -> ArtifactInput:
    fields: dict[str, object] = {
        "data": data,
        "source_type": EvidenceType.UPLOADED_DOCUMENT,
        "origin": EvidenceOrigin.USER_STATED,
        "evidence_label": EvidenceLabel.USER_ATTESTED,
        "media_type": "text/plain",
        "collection_method": "user_upload",
        "processing_authorization": PERMITTED,
        "sharing_authorization": PERMITTED,
        "case_id": case.id if case else None,
    }
    fields.update(overrides)
    return ArtifactInput(**fields)  # type: ignore[arg-type]


def _transcript(
    session: Session, store: LocalArtifactStore, settings: Settings, case: Case
) -> EvidenceArtifact:
    return ingest_artifact(
        session, store, settings, case.tenant_id,
        _doc(
            case, b"hello this is acme lending", source_type=EvidenceType.AI_TRANSCRIPT,
            origin=EvidenceOrigin.DERIVED, evidence_label=EvidenceLabel.MODEL_INFERRED,
        ),
        "system",
    )  # fmt: skip


def test_ingest_records_digest_authorization_and_retention(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    clock = FrozenClock()
    set_clock(clock)
    tenant = _tenant(session)
    case = _case(session, tenant)
    art = ingest_artifact(
        session, store, app_settings, tenant.id,
        _doc(case, provenance={"note": "screenshot of voicemail"}), ACTOR,
    )  # fmt: skip
    assert art.sha256 == sha256_hex(b"hello world")
    assert art.byte_length == 11
    assert art.media_type == "text/plain"
    assert art.origin == "USER_STATED" and art.evidence_label == "USER_ATTESTED"
    assert art.collection_method == "user_upload"
    assert art.collected_at == clock.current
    assert art.expires_at == clock.current + timedelta(days=app_settings.retention_case_days)
    assert art.processing_authorization == "PERMITTED" and art.sharing_authorization == "PERMITTED"
    assert art.provenance == {"note": "screenshot of voicemail"}
    assert art.storage_key and store.get(art.storage_key) == b"hello world"
    topics = {e.topic for e in session.execute(select(LedgerEvent)).scalars()}
    assert "evidence.created" in topics


def test_audio_and_transcripts_require_processing_authorization(
    session: Session, store: LocalArtifactStore, app_settings: Settings, tmp_path: object
) -> None:
    tenant = _tenant(session)
    case = _case(session, tenant)
    for source in (EvidenceType.AUDIO_RECORDING, EvidenceType.AI_TRANSCRIPT):
        with pytest.raises(PolicyBlockedError):
            ingest_artifact(
                session, store, app_settings, tenant.id,
                _doc(
                    case,
                    source_type=source,
                    processing_authorization=AuthorizationState.NOT_EVALUATED,
                ),
                "system",
            )  # fmt: skip
    assert session.execute(select(EvidenceArtifact)).first() is None
    assert not any(p.is_file() for p in store.root.rglob("*"))


def test_retention_defaults_differ_by_type(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    clock = FrozenClock()
    set_clock(clock)
    tenant = _tenant(session)
    audio = ingest_artifact(
        session, store, app_settings, tenant.id,
        _doc(None, source_type=EvidenceType.AUDIO_RECORDING, media_type="audio/wav"), "system",
    )  # fmt: skip
    assert audio.expires_at == clock.current + timedelta(days=app_settings.retention_recording_days)


def test_taxonomy_enforced_on_ingest(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    with pytest.raises(UnprocessableError):
        ingest_artifact(
            session, store, app_settings, tenant.id,
            _doc(None, origin=EvidenceOrigin.CALLER_CLAIMED, evidence_label=EvidenceLabel.OBSERVED),
            ACTOR,
        )  # fmt: skip


def test_integrity_detects_tamper_and_missing(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    art = ingest_artifact(session, store, app_settings, tenant.id, _doc(), ACTOR)
    assert verify_integrity(session, store, tenant.id, art.id).status == OK
    assert art.storage_key
    path = store.root / art.storage_key
    path.write_bytes(b"tampered!!!")
    report = verify_integrity(session, store, tenant.id, art.id)
    assert report.status == MISMATCH and not report.ok
    path.unlink()
    assert verify_integrity(session, store, tenant.id, art.id).status == MISSING


def test_cross_tenant_artifact_access_is_not_found(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    a, b = _tenant(session, "A"), _tenant(session, "B")
    art = ingest_artifact(session, store, app_settings, a.id, _doc(), ACTOR)
    with pytest.raises(NotFoundError):
        get_artifact(session, b.id, art.id)
    with pytest.raises(NotFoundError):
        verify_integrity(session, store, b.id, art.id)
    with pytest.raises(NotFoundError):
        redact_artifact(session, store, app_settings, b.id, art.id, RedactionSpec(), ACTOR)
    with pytest.raises(NotFoundError):
        set_legal_hold(session, b.id, art.id, True, ACTOR, "x")
    with pytest.raises(NotFoundError):
        delete_artifacts(session, store, b.id, art.id, ACTOR, "x")
    with pytest.raises(NotFoundError):  # cannot attach to another tenant's case
        ingest_artifact(session, store, app_settings, b.id, _doc(_case(session, a)), ACTOR)
    assert get_artifact(session, a.id, art.id).deleted_at is None


def test_sharing_requires_explicit_authorization(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    art = ingest_artifact(
        session, store, app_settings, tenant.id,
        _doc(None, sharing_authorization=AuthorizationState.NOT_EVALUATED), ACTOR,
    )  # fmt: skip
    with pytest.raises(ForbiddenError):
        require_sharing(art)
    set_authorization(
        session, tenant.id, art.id, field="sharing_authorization", state=PERMITTED, actor=ACTOR
    )
    require_sharing(art)


def test_redaction_creates_derivative_and_never_touches_original(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    case = _case(session, tenant)
    text = b"Call me at +1 415 555 0177 -- Jane Roe said hi to JANE ROE"
    original = ingest_artifact(
        session, store, app_settings, tenant.id, _doc(case, text, provenance={"k": 1}), ACTOR
    )
    derived = redact_artifact(
        session, store, app_settings, tenant.id, original.id, RedactionSpec(["jane roe"]), ACTOR
    )
    assert original.storage_key and derived.storage_key
    assert store.get(original.storage_key) == text
    assert original.sha256 == sha256_hex(text) and original.redaction_state == "NONE"
    redacted = store.get(derived.storage_key).decode()
    assert "Jane" not in redacted and "415" not in redacted and "[REDACTED]" in redacted
    assert derived.derived_from_id == original.id and derived.id != original.id
    assert derived.origin == "DERIVED" and derived.redaction_state == "REDACTED_DERIVATIVE"
    assert derived.provenance["derived_from"]["sha256"] == original.sha256
    report = derived.provenance["redaction"]
    assert report["terms"][0]["occurrences"] == 2
    assert "jane" not in json.dumps(report).lower()  # terms stored as digests only


def test_redaction_of_binary_needs_supplied_replacement(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    original = ingest_artifact(
        session,
        store,
        app_settings,
        tenant.id,
        _doc(None, b"\x89PNG....", media_type="image/png"),
        ACTOR,
    )
    with pytest.raises(UnprocessableError):
        redact_artifact(
            session, store, app_settings, tenant.id, original.id, RedactionSpec(), ACTOR
        )
    derived = redact_artifact(
        session, store, app_settings, tenant.id, original.id,
        RedactionSpec(replacement_content=b"\x89PNG-redacted"), ACTOR,
    )  # fmt: skip
    assert derived.provenance["redaction"]["method"] == "supplied_replacement"


def test_transcript_revisions_keep_originals_and_reference_prior(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    case = _case(session, tenant)
    v1 = _transcript(session, store, app_settings, case)
    v2 = revise_transcript(
        session,
        store,
        app_settings,
        tenant.id,
        v1.id,
        "hello this is ACME Lending",
        "ASR error",
        ACTOR,
    )
    v3 = revise_transcript(
        session,
        store,
        app_settings,
        tenant.id,
        v2.id,
        "hello, this is Acme Lending.",
        "punct",
        ACTOR,
    )
    assert v1.storage_key and v2.storage_key
    assert store.get(v1.storage_key) == b"hello this is acme lending"  # original ASR output intact
    assert (v2.supersedes_id, v3.supersedes_id) == (v1.id, v2.id)
    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    assert v2.provenance["correction"]["prior_sha256"] == v1.sha256
    assert [a.id for a in transcript_versions(session, tenant.id, v2.id)] == [v1.id, v2.id, v3.id]
    with pytest.raises(ConflictError):  # revising an already superseded version is refused
        revise_transcript(session, store, app_settings, tenant.id, v1.id, "x", "y", ACTOR)
    topics = {e.topic for e in session.execute(select(LedgerEvent)).scalars()}
    assert "correction.recorded" in topics


def test_only_transcripts_can_be_revised(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    art = ingest_artifact(session, store, app_settings, tenant.id, _doc(), ACTOR)
    with pytest.raises(UnprocessableError):
        revise_transcript(session, store, app_settings, tenant.id, art.id, "x", "y", ACTOR)


def test_expire_sweep_deletes_objects_and_tombstones_rows(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    clock = FrozenClock()
    set_clock(clock)
    tenant = _tenant(session)
    case = _case(session, tenant)
    short = ingest_artifact(
        session, store, app_settings, tenant.id,
        _doc(case, b"short", retention_days=1, provenance={"who": "someone"}), ACTOR,
    )  # fmt: skip
    keep = ingest_artifact(session, store, app_settings, tenant.id, _doc(case, b"keep"), ACTOR)
    held = ingest_artifact(
        session, store, app_settings, tenant.id, _doc(case, b"held", retention_days=1), ACTOR
    )
    set_legal_hold(session, tenant.id, held.id, True, ACTOR, "litigation")
    clock.advance(days=2)
    result = expire_sweep(session, store, tenant.id)
    assert result.deleted_ids == [str(short.id)] and result.held_ids == [str(held.id)]
    assert short.storage_key is None and short.deleted_at == clock.current
    assert short.sha256 == sha256_hex(b"short")  # non-personal audit kept
    assert short.provenance == {
        "tombstone": {
            "reason": "retention_expired", "deleted_at": clock.current.isoformat(),
            "scrubbed_keys": ["who"],
        }
    }  # fmt: skip
    assert held.deleted_at is None and held.storage_key and store.exists(held.storage_key)
    assert keep.deleted_at is None
    assert sum(1 for p in store.root.rglob("*") if p.is_file()) == 2
    again = expire_sweep(session, store, tenant.id)  # idempotent
    assert again.deleted_ids == []
    topics = [e.topic for e in session.execute(select(LedgerEvent)).scalars()]
    assert topics.count("artifact.expired") == 1


def test_sweep_is_tenant_scoped(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    clock = FrozenClock()
    set_clock(clock)
    a, b = _tenant(session, "A"), _tenant(session, "B")
    art_b = ingest_artifact(session, store, app_settings, b.id, _doc(None, retention_days=1), ACTOR)
    clock.advance(days=3)
    assert expire_sweep(session, store, a.id).deleted_ids == []
    assert art_b.deleted_at is None


def test_legal_hold_blocks_deletion_without_bypass(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    case = _case(session, tenant)
    original = ingest_artifact(
        session, store, app_settings, tenant.id, _doc(case, b"abc def"), ACTOR
    )
    derived = redact_artifact(
        session, store, app_settings, tenant.id, original.id, RedactionSpec(["abc"]), ACTOR
    )
    set_legal_hold(session, tenant.id, original.id, True, ACTOR, "hold")
    assert derived.legal_hold is True  # hold covers the whole family
    result = delete_artifacts(session, store, tenant.id, derived.id, ACTOR, "user asked")
    assert result.deleted == [] and len(result.retained) == 2
    assert original.deleted_at is None and original.storage_key
    assert store.exists(original.storage_key)
    set_legal_hold(session, tenant.id, original.id, False, ACTOR, "released")
    result = delete_artifacts(session, store, tenant.id, original.id, ACTOR, "user asked")
    assert len(result.deleted) == 2 and original.storage_key is None


def test_deletion_request_discloses_limits_and_respects_hold(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    user_id = _user(session, tenant)
    held = ingest_artifact(session, store, app_settings, tenant.id, _doc(None, b"held"), ACTOR)
    free = ingest_artifact(session, store, app_settings, tenant.id, _doc(None, b"free"), ACTOR)
    set_legal_hold(session, tenant.id, held.id, True, ACTOR, "hold")
    denied = request_artifact_deletion(session, store, tenant.id, user_id, held.id, "withdraw")
    assert denied.status == "DENIED_LEGAL_HOLD" and denied.result["retained"]
    done = request_artifact_deletion(session, store, tenant.id, user_id, free.id, "withdraw")
    assert done.status == "COMPLETED" and free.deleted_at is not None
    assert any("legal hold" in line.lower() for line in done.result["limits"])
    assert any("exported" in line.lower() for line in done.result["limits"])


def test_withdrawal_revokes_authorizations_and_blocks_new_ingest(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    tenant = _tenant(session)
    user_id = _user(session, tenant)
    art = ingest_artifact(session, store, app_settings, tenant.id, _doc(None), ACTOR)
    meta = ingest_artifact(
        session, store, app_settings, tenant.id,
        _doc(
            None,
            b"{}",
            source_type=EvidenceType.SIGNALING_METADATA,
            origin=EvidenceOrigin.ORIGINAL,
            evidence_label=EvidenceLabel.OBSERVED,
        ),
        "system",
    )  # fmt: skip
    request = withdraw_processing(session, tenant.id, user_id, "stop")
    assert request.status == "COMPLETED"
    assert art.processing_authorization == "REVOKED" and art.sharing_authorization == "REVOKED"
    assert meta.processing_authorization == "PERMITTED"  # lawful signaling metadata (ENF-008)
    with pytest.raises(PolicyBlockedError):
        ingest_artifact(session, store, app_settings, tenant.id, _doc(None, b"more"), ACTOR)
    with pytest.raises(ForbiddenError):
        require_sharing(art)


def test_data_export_is_tenant_scoped_and_lists_limits(
    session: Session, store: LocalArtifactStore, app_settings: Settings
) -> None:
    a, b = _tenant(session, "A"), _tenant(session, "B")
    user_id = _user(session, a)
    mine = ingest_artifact(session, store, app_settings, a.id, _doc(None), ACTOR)
    theirs = ingest_artifact(session, store, app_settings, b.id, _doc(None, b"other"), ACTOR)
    payload = build_data_export(session, a.id, user_id)
    ids = {item["id"] for item in payload["evidence_artifacts"]}
    assert str(mine.id) in ids and str(theirs.id) not in ids
    assert payload["deletion_limits"]
    assert "storage_key" not in json.dumps(payload)
    kinds = [r.kind for r in session.execute(select(DataSubjectRequest)).scalars()]
    assert "EXPORT" in kinds


def test_expire_sweep_job_handler(
    session: Session, engine: Engine, store: LocalArtifactStore, app_settings: Settings
) -> None:
    clock = FrozenClock()
    set_clock(clock)
    tenant = _tenant(session)
    art = ingest_artifact(
        session, store, app_settings, tenant.id, _doc(None, retention_days=1), ACTOR
    )
    clock.advance(days=5)
    assert schedule_expire_sweep(session, tenant.id) is True
    assert schedule_expire_sweep(session, tenant.id) is False  # one per tenant per day
    session.commit()
    load_handler_modules()
    assert run_one(make_session_factory(engine), ["evidence.expire_sweep"]) is True
    session.expire_all()
    assert art.deleted_at is not None and art.storage_key is None
    job, created = enqueue(session, tenant.id, "evidence.expire_sweep", {}, "other-key")
    assert created and job.kind == "evidence.expire_sweep"
