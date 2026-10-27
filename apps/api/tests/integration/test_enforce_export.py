from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from callshield.casebuilder import (
    UserAccount,
    build_timeline,
    import_user_accounts,
    request_export,
    run_incident_pipeline,
)
from callshield.casebuilder.export import (
    TOKEN_AUDIENCE,
    create_download_token,
    decode_download_token,
    expire_exports,
    export_bytes,
    get_export,
    run_export,
)
from callshield.core.clock import FrozenClock, set_clock, utcnow
from callshield.core.config import Settings
from callshield.core.errors import ConflictError, NotFoundError, UnauthorizedError
from callshield.core.hashing import sha256_hex, sha256_json
from callshield.db.models import Case, CaseAssessment, Tenant
from callshield.db.models.enforce_ext import CaseExport
from callshield.db.session import make_session_factory
from callshield.domain.enums import (
    AuthorizationState,
    EvidenceLabel,
    EvidenceOrigin,
    EvidenceType,
)
from callshield.evidence import ArtifactInput, RedactionSpec, ingest_artifact, redact_artifact
from callshield.evidence.store import LocalArtifactStore
from callshield.worker.runner import load_handler_modules, run_one
from tests.enforce_support import (
    CALLER_A,
    seed_incident,
    seed_line,
    seed_opt_out,
    seed_resolution,
)

pytest_plugins = ("tests.enforce_support",)

PERMITTED = AuthorizationState.PERMITTED
SECRET_MARKER = "OTHER-TENANT-SECRET-MARKER"
SPEC_FIELDS = {
    "schema_version", "case_id", "snapshot_id", "created_at_utc", "tenant_id_pseudonym",
    "included_artifacts", "excluded_artifacts_summary", "generator_version", "hash_algorithm",
    "export_digest", "review_status",
}  # fmt: skip
ARTIFACT_FIELDS = {
    "artifact_id", "sha256", "byte_length", "mime_type", "origin", "collection_method",
    "collected_at_utc", "consent_status", "redaction_state",
}  # fmt: skip


def _doc(
    case: Case, data: bytes, sharing: AuthorizationState = PERMITTED, **kw: object
) -> ArtifactInput:
    fields: dict[str, object] = {
        "data": data, "source_type": EvidenceType.UPLOADED_DOCUMENT,
        "origin": EvidenceOrigin.USER_STATED, "evidence_label": EvidenceLabel.USER_ATTESTED,
        "media_type": "text/plain", "collection_method": "user_upload",
        "processing_authorization": PERMITTED, "sharing_authorization": sharing, "case_id": case.id,
    }  # fmt: skip
    fields.update(kw)
    return ArtifactInput(**fields)  # type: ignore[arg-type]


class World:
    """A tenant with a populated case plus a second tenant that must never leak in."""

    def __init__(self, session: Session, store: LocalArtifactStore, settings: Settings) -> None:
        self.session, self.store, self.settings = session, store, settings
        self.tenant = Tenant(name="Export tenant")
        self.other = Tenant(name="Other tenant")
        session.add_all([self.tenant, self.other])
        session.flush()
        line = seed_line(session, self.tenant.id)
        incident = seed_incident(session, self.tenant.id, line.id, claimed="Acme Lending")
        result = run_incident_pipeline(
            session, self.tenant.id, incident.id, store=store, settings=settings
        )
        assert result.case_id
        case = session.get(Case, result.case_id)
        assert case is not None
        self.case: Case = case
        resolution = seed_resolution(session, self.tenant.id, line.id, self.case.id, attempts=3)
        seed_opt_out(session, self.tenant.id, resolution.id, "DELIVERED")
        self.shared = ingest_artifact(
            session,
            store,
            settings,
            self.tenant.id,
            _doc(self.case, b"shared screenshot text"),
            "user:u",
        )
        self.private = ingest_artifact(
            session, store, settings, self.tenant.id,
            _doc(self.case, b"private note", AuthorizationState.NOT_EVALUATED), "user:u",
        )  # fmt: skip
        self.unselected = ingest_artifact(
            session, store, settings, self.tenant.id, _doc(self.case, b"unselected"), "user:u"
        )
        self._seed_other_tenant()

    def _seed_other_tenant(self) -> None:
        line = seed_line(self.session, self.other.id, e164="+14155550199")
        inc = seed_incident(self.session, self.other.id, line.id, claimed=SECRET_MARKER)
        res = run_incident_pipeline(
            self.session, self.other.id, inc.id, store=self.store, settings=self.settings
        )
        other_case = self.session.get(Case, res.case_id)
        assert other_case is not None
        self.other_case: Case = other_case
        self.other_art = ingest_artifact(
            self.session, self.store, self.settings, self.other.id,
            _doc(self.other_case, SECRET_MARKER.encode()), "user:u",
        )  # fmt: skip

    def export(
        self, ids: list[uuid.UUID] | None = None, **kw: object
    ) -> tuple[CaseExport, zipfile.ZipFile]:
        export, _ = request_export(
            self.session,
            self.tenant.id,
            self.case.id,
            "user:u",
            artifact_ids=ids or [],
            **kw,  # type: ignore[arg-type]
        )
        run_export(self.session, self.store, self.tenant.id, export.id)
        data = export_bytes(self.store, export)
        return export, zipfile.ZipFile(io.BytesIO(data))


@pytest.fixture
def world(session: Session, store: LocalArtifactStore, app_settings: Settings) -> World:
    return World(session, store, app_settings)


def _manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    return json.loads(archive.read("manifest.json"))  # type: ignore[no-any-return]


def test_export_contains_every_spec_file_and_manifest_field(world: World) -> None:
    export, archive = world.export([world.shared.id])
    names = set(archive.namelist())
    assert {
        "manifest.json", "README_EVIDENCE.txt", "case_summary.html", "case_summary.txt",
        "chronology.csv", "calls.csv", "opt_out_requests.csv", "operator_identity_assessment.json",
        "issue_spotting.json", "audit_extract.json", "redactions.json",
    } <= names  # fmt: skip
    assert not any(n.endswith(".pdf") for n in names)  # PDF rendering is a separate component
    manifest = _manifest(archive)
    assert set(manifest) >= SPEC_FIELDS
    assert manifest["schema_version"] and manifest["hash_algorithm"] == "sha256"
    assert manifest["case_id"] == str(world.case.id) and manifest["snapshot_id"] == str(
        export.snapshot_id
    )
    included = manifest["included_artifacts"]
    assert isinstance(included, list) and len(included) == 1
    assert set(included[0]) >= ARTIFACT_FIELDS
    assert included[0]["artifact_id"] == str(world.shared.id)
    assert export.status == "COMPLETED" and export.export_digest == manifest["export_digest"]


def test_every_attachment_and_file_is_hashed_in_the_manifest(world: World) -> None:
    extra = ingest_artifact(
        world.session, world.store, world.settings, world.tenant.id,
        _doc(world.case, b"\x89PNG-bytes", media_type="image/png"), "user:u",
    )  # fmt: skip
    _, archive = world.export([world.shared.id, extra.id])
    manifest = _manifest(archive)
    attachments = [n for n in archive.namelist() if n.startswith("attachments/")]
    assert len(attachments) == 2
    hashed = {row["path"]: row for row in manifest["files"]}  # type: ignore[attr-defined]
    for name in attachments:  # 100% of attachments recorded with the digest of the zipped bytes
        assert hashed[name]["sha256"] == sha256_hex(archive.read(name))
    for name in set(archive.namelist()) - {"manifest.json"}:
        assert hashed[name]["sha256"] == sha256_hex(archive.read(name))
        assert hashed[name]["byte_length"] == len(archive.read(name))
    for row in manifest["included_artifacts"]:  # type: ignore[attr-defined]
        assert row["sha256"] == sha256_hex(archive.read(row["path"]))
    body = {k: v for k, v in manifest.items() if k != "export_digest"}
    assert sha256_json(body) == manifest["export_digest"]


def test_only_authorized_selected_attachments_with_explicit_omissions(world: World) -> None:
    _, archive = world.export([world.shared.id, world.private.id])
    manifest = _manifest(archive)
    ids = {r["artifact_id"] for r in manifest["included_artifacts"]}  # type: ignore[attr-defined]
    assert ids == {str(world.shared.id)}
    summary = manifest["excluded_artifacts_summary"]
    reasons = {i["artifact_id"]: i["reason"] for i in summary["items"]}  # type: ignore[index]
    assert reasons[str(world.private.id)] == "SHARING_NOT_AUTHORIZED"
    assert reasons[str(world.unselected.id)] == "NOT_SELECTED"
    assert summary["total_excluded"] == len(reasons)  # type: ignore[index]
    blob = b"".join(archive.read(n) for n in archive.namelist() if n.startswith("attachments/"))
    assert b"private note" not in blob and b"unselected" not in blob


def test_foreign_and_unknown_artifact_ids_are_never_attached(world: World) -> None:
    _, archive = world.export([world.other_art.id, uuid.uuid4()])
    manifest = _manifest(archive)
    assert manifest["included_artifacts"] == []
    assert not any(n.startswith("attachments/") for n in archive.namelist())
    everything = b"".join(archive.read(n) for n in archive.namelist())
    assert SECRET_MARKER.encode() not in everything
    # A requested id is echoed back only as NOT_FOUND_IN_CASE: no oracle for foreign ids.
    items = manifest["excluded_artifacts_summary"]["items"]  # type: ignore[index]
    echoed = {i["artifact_id"]: i for i in items if i["reason"] == "NOT_FOUND_IN_CASE"}
    assert str(world.other_art.id) in echoed
    assert echoed[str(world.other_art.id)]["source_type"] == "UNKNOWN"
    assert world.other_art.sha256 and world.other_art.sha256.encode() not in everything


def test_export_has_no_other_tenant_data_ids_or_secrets(world: World) -> None:
    _, archive = world.export([world.shared.id])
    everything = b"".join(archive.read(n) for n in archive.namelist())
    for needle in (
        SECRET_MARKER, str(world.other.id), str(world.other_case.id), "+14155550199",
        world.settings.jwt_secret.get_secret_value(), "password_hash", "Authorization",
    ):  # fmt: skip
        assert needle.encode() not in everything, needle
    assert str(world.tenant.id).encode() not in everything  # pseudonymized, not the raw id
    manifest = _manifest(archive)
    assert manifest["tenant_id_pseudonym"] and manifest["tenant_id_pseudonym"] != str(
        world.tenant.id
    )


def test_readme_and_summary_make_no_forbidden_claims(world: World) -> None:
    _, archive = world.export()
    readme = archive.read("README_EVIDENCE.txt").decode()
    assert "makes no claim of notarization, forensic certification, admissibility" in readme
    assert "detect later modification" in readme
    for name in ("README_EVIDENCE.txt", "case_summary.txt", "case_summary.html"):
        text = archive.read(name).decode().lower()
        for banned in ("is admissible", "court-ready", "legally valid", "forensically certified",
                       "notarized by", "proves", "violation proven", "guilty"):  # fmt: skip
            assert banned not in text, (name, banned)
    html = archive.read("case_summary.html").decode()
    assert html.startswith("<!doctype html>") and "<script" not in html.lower()


def test_chronology_calls_and_opt_outs_csvs(world: World) -> None:
    _, archive = world.export()
    chrono = list(csv.DictReader(io.StringIO(archive.read("chronology.csv").decode())))
    entries = build_timeline(world.session, world.tenant.id, world.case.id)
    assert {r["entry_id"] for r in chrono} <= {e.entry_id for e in entries}
    assert sum(r["kind"] == "CONTACT_ATTEMPT" for r in chrono) == 3
    assert {r["source_label"] for r in chrono} <= {label.value for label in EvidenceLabel}
    calls = list(csv.DictReader(io.StringIO(archive.read("calls.csv").decode())))
    assert len(calls) == 1 and calls[0]["displayed_caller_id"] == CALLER_A
    assert "not a verified calling party" in calls[0]["source_label"]
    optouts = list(csv.DictReader(io.StringIO(archive.read("opt_out_requests.csv").decode())))
    assert [r["row_type"] for r in optouts].count("ATTEMPT") == 3
    assert any(r["note"] == "acknowledgment is not suppression" for r in optouts)


def test_mask_numbers_option(world: World) -> None:
    _, archive = world.export(mask_numbers=True)
    calls = archive.read("calls.csv").decode()
    assert CALLER_A not in calls and "+1415***" in calls
    identity = json.loads(archive.read("operator_identity_assessment.json"))
    assert CALLER_A not in json.dumps(identity)


def test_identity_assessment_keeps_claimed_and_observed_separate(world: World) -> None:
    _, archive = world.export()
    doc = json.loads(archive.read("operator_identity_assessment.json"))
    assert doc["verified_operator_identity"] is None
    assert doc["claimed_entities"][0] == {
        "name": "acme lending",
        "label": "CLAIMED",
        "verified": False,
    }
    assert doc["observed_numbers"][0]["label"] == "OBSERVED"
    assert "spoofed" in doc["note"]


def test_issue_spotting_empty_then_populated_and_attestation_optional(world: World) -> None:
    _, archive = world.export()
    empty = json.loads(archive.read("issue_spotting.json"))
    assert empty["assessments"] == [] and "No assessment has been run" in empty["note"]
    assert "user_attestation.json" not in archive.namelist()
    assert any("user_attestation.json" in o for o in _manifest(archive)["omitted_files"])  # type: ignore[attr-defined]
    world.session.add(
        CaseAssessment(
            tenant_id=world.tenant.id, case_id=world.case.id, ruleset_version="rules-v1",
            issue_type="DNC_ENTITY_SPECIFIC", status="INSUFFICIENT_INFORMATION",
            supporting_event_ids=[], missing_requirements=["seller identity"],
        )
    )  # fmt: skip
    import_user_accounts(
        world.session, world.store, world.settings, world.tenant.id, world.case.id,
        [UserAccount("I asked them to stop")], "user:u",
    )  # fmt: skip
    world.session.flush()
    # an imported statement is not shareable until the user authorizes it
    _, second = world.export()
    assert "user_attestation.json" not in second.namelist()
    populated = json.loads(second.read("issue_spotting.json"))
    assert populated["assessments"][0]["status"] == "INSUFFICIENT_INFORMATION"
    assert "Not a legal determination" in populated["note"]


def test_authorized_user_statement_appears_in_attestation_file(world: World) -> None:
    created = import_user_accounts(
        world.session, world.store, world.settings, world.tenant.id, world.case.id,
        [UserAccount("I asked them to stop on a call")], "user:u",
    )  # fmt: skip
    created[0].sharing_authorization = "PERMITTED"
    _, archive = world.export()
    doc = json.loads(archive.read("user_attestation.json"))
    assert doc["label"] == "USER_ATTESTED"
    assert doc["statements"][0]["content"]["narrative"] == "I asked them to stop on a call"


def test_redactions_json_lists_derivatives_with_digests_only(world: World) -> None:
    original = ingest_artifact(
        world.session, world.store, world.settings, world.tenant.id,
        _doc(world.case, b"Contact Jane Roe at home"), "user:u",
    )  # fmt: skip
    derived = redact_artifact(
        world.session, world.store, world.settings, world.tenant.id, original.id,
        RedactionSpec(["Jane Roe"]), "user:u",
    )  # fmt: skip
    _, archive = world.export([derived.id])
    doc = json.loads(archive.read("redactions.json"))
    item = next(i for i in doc["redactions"] if i["derived_artifact_id"] == str(derived.id))
    assert item["original_artifact_id"] == str(original.id) and item["derived_included_in_export"]
    assert "Jane" not in json.dumps(doc)
    attachments = b"".join(
        archive.read(n) for n in archive.namelist() if n.startswith("attachments/")
    )
    assert b"Jane Roe" not in attachments and b"[REDACTED]" in attachments


def test_tampered_or_deleted_originals_are_excluded_not_attached(world: World) -> None:
    assert world.shared.storage_key
    (world.store.root / world.shared.storage_key).write_bytes(b"tampered")
    _, archive = world.export([world.shared.id])
    manifest = _manifest(archive)
    assert manifest["included_artifacts"] == []
    reasons = [i["reason"] for i in manifest["excluded_artifacts_summary"]["items"]]  # type: ignore[index]
    assert "INTEGRITY_CHECK_FAILED" in reasons


def test_export_versions_are_tracked_and_idempotent_by_key(world: World) -> None:
    first, _ = request_export(
        world.session, world.tenant.id, world.case.id, "user:u", client_key="abc"
    )
    replay, created = request_export(
        world.session, world.tenant.id, world.case.id, "user:u", client_key="abc"
    )
    assert replay.id == first.id and created is False
    second, _ = request_export(world.session, world.tenant.id, world.case.id, "user:u")
    assert (first.version, second.version) == (1, 2)
    assert first.status == "QUEUED"
    run_export(world.session, world.store, world.tenant.id, first.id)
    run_export(world.session, world.store, world.tenant.id, first.id)  # idempotent
    assert first.status == "COMPLETED" and first.zip_sha256 and first.byte_length
    assert (
        _manifest(zipfile.ZipFile(io.BytesIO(export_bytes(world.store, first))))["export_version"]
        == 1
    )


def test_export_runs_as_a_worker_job(world: World, engine: Engine) -> None:
    export, _ = request_export(world.session, world.tenant.id, world.case.id, "user:u")
    world.session.commit()
    load_handler_modules()
    assert run_one(make_session_factory(engine), ["case.export"]) is True
    world.session.expire_all()
    assert export.status == "COMPLETED"
    assert get_export(world.session, world.tenant.id, world.case.id, export.id).export_digest


def test_export_lookup_is_tenant_and_case_scoped(world: World) -> None:
    export, _ = world.export()
    with pytest.raises(NotFoundError):
        get_export(world.session, world.other.id, world.case.id, export.id)
    with pytest.raises(NotFoundError):
        get_export(world.session, world.tenant.id, world.other_case.id, export.id)
    with pytest.raises(NotFoundError):
        request_export(world.session, world.other.id, world.case.id, "user:u")


def test_export_zip_expires_and_is_deleted(world: World) -> None:
    clock = FrozenClock(utcnow())
    set_clock(clock)
    export, _ = world.export()
    assert export.storage_key and world.store.exists(export.storage_key)
    clock.advance(days=31)
    with pytest.raises(ConflictError):
        export_bytes(world.store, export)
    assert expire_exports(world.session, world.store, world.tenant.id) == 1
    assert export.storage_key is None and export.deleted_at is not None
    with pytest.raises(ConflictError):
        export_bytes(world.store, export)


def test_export_integrity_is_verified_on_download(world: World) -> None:
    export, _ = world.export()
    assert export.storage_key
    (world.store.root / export.storage_key).write_bytes(b"swapped")
    with pytest.raises(ConflictError) as excinfo:
        export_bytes(world.store, export)
    assert excinfo.value.code == "EXPORT_INTEGRITY_FAILED"


def test_download_tokens_are_scoped_and_expiring(world: World) -> None:
    export, _ = world.export()
    settings = world.settings
    token, expires = create_download_token(settings, world.tenant.id, export.id, ttl_seconds=60)
    assert decode_download_token(settings, token) == (world.tenant.id, export.id)
    assert expires > utcnow()
    with pytest.raises(UnauthorizedError):
        decode_download_token(settings, token + "x")
    other_secret = Settings(jwt_secret="a-completely-different-secret-value-0123456789")
    with pytest.raises(UnauthorizedError):
        decode_download_token(other_secret, token)
    old, _ = create_download_token(
        settings, world.tenant.id, export.id, now=utcnow() - timedelta(hours=2)
    )
    with pytest.raises(UnauthorizedError):
        decode_download_token(settings, old)
    access_like = jwt.encode(  # an ordinary access token must not work as a download token
        {"sub": "x", "tid": str(world.tenant.id), "eid": str(export.id), "exp": 9999999999},
        settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm,
    )  # fmt: skip
    with pytest.raises(UnauthorizedError):
        decode_download_token(settings, access_like)
    assert TOKEN_AUDIENCE not in token  # audience lives inside the signed claims only
