from __future__ import annotations

import base64
import io
import uuid
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from callshield.casebuilder.export import create_download_token
from callshield.core.clock import utcnow
from callshield.core.config import Settings
from callshield.db.models import Case
from callshield.worker.runner import load_handler_modules, run_one
from tests.conftest import TenantUser, TenantUserFactory
from tests.enforce_support import (
    seed_incident,
    seed_line,
)

pytest_plugins = ("tests.enforce_support",)

B64 = base64.b64encode


class World:
    def __init__(self, client: TestClient, user: TenantUser) -> None:
        self.client, self.user = client, user
        self.factory: sessionmaker[Session] = client.app.state.session_factory  # type: ignore[attr-defined]
        with self.factory() as session:
            line = seed_line(session, user.tenant_id)
            self.line_id = line.id
            self.incidents = [
                seed_incident(session, user.tenant_id, line.id, offset_minutes=m * 30).id
                for m in range(3)
            ]
            session.commit()
        resp = self.post("/v1/cases", {"protected_line_id": str(self.line_id), "title": "Case"})
        assert resp.status_code == 201, resp.text
        self.case_id = resp.json()["id"]
        link = self.post(
            f"/v1/cases/{self.case_id}/incidents:link", {"incident_id": str(self.incidents[0])}
        )
        assert link.status_code == 200, link.text
        self.artifact_id = self.upload(b"first evidence").json()["id"]

    def post(self, path: str, body: dict[str, Any] | None = None, **kw: Any) -> Any:
        headers = {**self.user.headers, **kw.pop("headers", {})}
        return self.client.post(path, json=body or {}, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        headers = {**self.user.headers, **kw.pop("headers", {})}
        return self.client.get(path, headers=headers, **kw)

    def upload(self, data: bytes, **overrides: Any) -> Any:
        body = {
            "source_type": "UPLOADED_DOCUMENT",
            "content_base64": B64(data).decode(),
            "media_type": "text/plain",
            "processing_authorization": "PERMITTED",
            "sharing_authorization": "PERMITTED",
        }
        body.update(overrides)
        return self.post(f"/v1/cases/{self.case_id}/evidence", body)


@pytest.fixture
def world(
    client: TestClient,
    make_tenant_user: TenantUserFactory,
    store: object,
) -> World:
    return World(client, make_tenant_user("Owner"))


def _headers(user: TenantUser) -> dict[str, str]:
    return user.headers


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    some = uuid.uuid4()
    for method, path in [
        ("get", "/v1/cases"),
        ("post", "/v1/cases"),
        ("get", f"/v1/cases/{some}"),
        ("get", f"/v1/cases/{some}/timeline"),
        ("post", f"/v1/cases/{some}/exports"),
        ("get", f"/v1/evidence/{some}"),
        ("get", "/v1/me/data-export"),
    ]:
        assert getattr(client, method)(path).status_code == 401, (method, path)


def test_create_list_get_case(world: World) -> None:
    again = world.post(
        "/v1/cases", {"protected_line_id": str(world.line_id), "title": "x"},
        headers={"Idempotency-Key": "k1"},
    )  # fmt: skip
    assert again.status_code == 201  # no key on the first create, so a new case
    first = world.client.post(
        "/v1/cases", json={"protected_line_id": str(world.line_id), "title": "Keyed"},
        headers={**world.user.headers, "Idempotency-Key": "k2"},
    )  # fmt: skip
    replay = world.client.post(
        "/v1/cases", json={"protected_line_id": str(world.line_id), "title": "Keyed"},
        headers={**world.user.headers, "Idempotency-Key": "k2"},
    )  # fmt: skip
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    page = world.get("/v1/cases", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    opened = world.get("/v1/cases", params={"state": "OPENED"}).json()
    assert opened["total"] == 3
    assert world.get("/v1/cases", params={"state": "CLOSED"}).json()["total"] == 0
    assert world.get("/v1/cases", params={"state": "BOGUS"}).status_code == 422
    detail = world.get(f"/v1/cases/{world.case_id}").json()
    assert detail["case"]["state"] == "OPENED"
    assert [link["incident_id"] for link in detail["incident_links"]] == [str(world.incidents[0])]
    assert detail["repeat_contact_verified"] is False


def test_sufficiency_checklist_lists_missing_sources_without_legal_score(world: World) -> None:
    body = world.get(f"/v1/cases/{world.case_id}").json()["evidence_sufficiency"]
    assert "opt_out_documented" in body["missing_sources"]
    assert "identity_independently_verified" in body["missing_sources"]
    flat = str({k: v for k, v in body.items() if k != "disclaimer"}).lower()
    for banned in ("percent", "probability", "validity", "likelihood", "strength", "score"):
        assert banned not in flat, banned
    assert "not a legal assessment" in body["disclaimer"]
    assert body["present_weight"] < body["total_weight"]
    assert all({"key", "weight", "status"} <= set(item) for item in body["items"])


def test_link_unlink_and_timeline_keep_history(world: World) -> None:
    other = str(world.incidents[1])
    linked = world.post(
        f"/v1/cases/{world.case_id}/incidents:link",
        {"incident_id": other, "user_confirms_same_operator": True, "reason": "same voice"},
    )
    assert linked.json()["changed"] is True
    unlinked = world.post(
        f"/v1/cases/{world.case_id}/incidents:unlink", {"incident_id": other, "reason": "wrong"}
    )
    assert unlinked.json() == {**unlinked.json(), "linked": False, "changed": True}
    path = f"/v1/cases/{world.case_id}/incidents:unlink"
    assert world.post(path, {"incident_id": other, "reason": "again"}).json()["changed"] is False
    assert (
        world.post(
            f"/v1/cases/{world.case_id}/incidents:unlink", {"incident_id": other}
        ).status_code
        == 422
    )
    timeline = world.get(f"/v1/cases/{world.case_id}/timeline").json()
    kinds = [e["kind"] for e in timeline["entries"]]
    assert kinds.count("LINK_CHANGE") == 3  # AUTO/USER link rows are all still visible
    assert kinds.count("INCIDENT") == 1  # only the currently linked incident
    assert set(timeline["source_labels"]) == {
        "OBSERVED", "CLAIMED", "MODEL_INFERRED", "USER_ATTESTED", "EXTERNALLY_VERIFIED",
    }  # fmt: skip
    stamps = [e["occurred_at"] for e in timeline["entries"]]
    assert stamps == sorted(stamps)
    foreign_line = world.client.post(
        f"/v1/cases/{world.case_id}/incidents:link", json={"incident_id": str(uuid.uuid4())},
        headers=world.user.headers,
    )  # fmt: skip
    assert foreign_line.status_code == 404


def test_reclassify_keeps_source_event(world: World) -> None:
    incident_id = str(world.incidents[0])
    resp = world.post(
        f"/v1/cases/{world.case_id}/incidents/{incident_id}/reclassify",
        {"label": "INFORMATIONAL", "reason": "it was my pharmacy"},
    )
    assert resp.status_code == 201 and resp.json()["source_incident_modified"] is False
    assert resp.json()["from"] == "UNSOLICITED_SALES" and resp.json()["to"] == "INFORMATIONAL"
    detail = world.get(f"/v1/cases/{world.case_id}").json()
    assert detail["incident_links"][0]["effective_label"] == "INFORMATIONAL"
    bad = world.post(
        f"/v1/cases/{world.case_id}/incidents/{incident_id}/reclassify", {"label": "NOPE"}
    )
    assert bad.status_code == 422


def test_transition_endpoint_only_exposes_early_states(world: World) -> None:
    case_id = world.case_id
    assert world.post(f"/v1/cases/{case_id}/transition", {"to_state": "INTAKE"}).status_code == 200
    forged = {
        "to_state": "USER_APPROVED", "line_authorized": True, "attestation_completed": True,
        "evidence_snapshot_digest": "x", "required_fields_complete": True,
        "sharing_confirmed": True, "destination_validated": True,
    }  # fmt: skip
    assert world.post(f"/v1/cases/{case_id}/transition", forged).status_code == 409
    skip = world.post(f"/v1/cases/{case_id}/transition", {"to_state": "REVIEW_REQUIRED"})
    assert skip.status_code == 409 and skip.json()["error"]["code"] == "INVALID_TRANSITION"
    ok = world.post(f"/v1/cases/{case_id}/transition", {"to_state": "EVIDENCE_COLLECTING"})
    assert ok.json()["state"] == "EVIDENCE_COLLECTING"
    stale = world.post(
        f"/v1/cases/{case_id}/transition",
        {"to_state": "REVIEW_REQUIRED", "expected_review_version": 1},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_REVIEW_VERSION"


def test_attribution_review_gate_over_http(
    world: World, make_tenant_user: TenantUserFactory
) -> None:
    case_id = world.case_id
    denied = world.post(
        f"/v1/cases/{case_id}/attribution-review",
        {"status": "CORROBORATED", "basis": "same number"},
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "INSUFFICIENT_INDEPENDENT_FEATURES"
    ok = world.post(
        f"/v1/cases/{case_id}/attribution-review", {"status": "DISPUTED", "basis": "user says no"}
    )
    assert ok.status_code == 200 and ok.json()["attribution_status"] == "DISPUTED"
    member = world.post(
        "/v1/users", {"email": "member@example.test", "password": "correct-horse-battery"}
    )
    assert member.status_code == 201
    login = world.client.post(
        "/v1/auth/login", json={"email": "member@example.test", "password": "correct-horse-battery"}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    blocked = world.client.post(
        f"/v1/cases/{case_id}/attribution-review", json={"status": "DISPUTED", "basis": "x"},
        headers=headers,
    )  # fmt: skip
    assert blocked.status_code == 403


def test_user_attested_import_endpoint(world: World) -> None:
    resp = world.post(
        f"/v1/cases/{world.case_id}/imports/user-attested",
        {"accounts": [{"narrative": "I called 11 times", "approximate_count": 11,
                       "stated_event_date": "2025-11-02", "category": "RETURN_CALL_ATTEMPT"}]},
    )  # fmt: skip
    assert resp.status_code == 201
    item = resp.json()["imported"][0]
    assert item["evidence_label"] == "USER_ATTESTED" and item["origin"] == "USER_STATED"
    assert item["provenance"]["no_observed_metadata"] is True
    entries = world.get(f"/v1/cases/{world.case_id}/timeline").json()["entries"]
    imp = next(e for e in entries if e["kind"] == "USER_IMPORT")
    assert imp["source_label"] == "USER_ATTESTED" and imp["occurred_at"].startswith("2025-11-02")


def test_upload_metadata_and_scoped_content(world: World) -> None:
    created = world.upload(b"voicemail transcript", provenance={"source": "phone screenshot"})
    assert created.status_code == 201
    art = created.json()
    assert art["evidence_label"] == "USER_ATTESTED" and art["origin"] == "USER_STATED"
    assert "storage_key" not in art and art["sha256"] and art["content_available"] is True
    meta = world.get(f"/v1/evidence/{art['id']}", params={"verify": "true"}).json()
    assert meta["integrity"]["status"] == "OK"
    content = world.get(f"/v1/evidence/{art['id']}/content")
    assert content.content == b"voicemail transcript"
    assert content.headers["content-disposition"].startswith("attachment")
    assert content.headers["x-content-sha256"] == art["sha256"]


def test_upload_validation_and_gates(world: World) -> None:
    assert world.upload(b"x", source_type="SIGNALING_METADATA").status_code == 422
    assert world.upload(b"x", origin="DERIVED").status_code == 422
    assert world.upload(b"x", evidence_label="OBSERVED").status_code == 422
    assert world.upload(b"x", evidence_label="EXTERNALLY_VERIFIED").status_code == 403
    assert world.upload(b"x", content_base64="@@not base64@@").status_code == 422
    blocked = world.upload(
        b"RIFF", source_type="AUDIO_RECORDING", processing_authorization="NOT_EVALUATED"
    )
    assert (
        blocked.status_code == 403
        and blocked.json()["error"]["code"] == "PERSISTENCE_NOT_AUTHORIZED"
    )
    ok = world.upload(b"RIFF", source_type="AUDIO_RECORDING", media_type="audio/wav")
    assert ok.status_code == 201
    claimed = world.upload(
        b"they said they are Acme", origin="CALLER_CLAIMED", source_type="USER_STATEMENT"
    )
    assert claimed.json()["evidence_label"] == "CLAIMED"
    missing_fields = world.post(
        f"/v1/cases/{world.case_id}/evidence",
        {"source_type": "SCREENSHOT", "content_base64": B64(b"x").decode()},
    )
    assert missing_fields.status_code == 422  # authorization fields are mandatory


def test_redact_endpoint_and_version_chain(world: World) -> None:
    original = world.upload(b"Call +1 415 555 0177 about Jane Roe").json()
    path = f"/v1/cases/{world.case_id}/evidence/{original['id']}/redact"
    resp = world.post(path, {"terms": ["Jane Roe"]})
    assert resp.status_code == 201
    derived = resp.json()["derived"]
    assert (
        derived["derived_from_id"] == original["id"]
        and derived["redaction_state"] == "REDACTED_DERIVATIVE"
    )
    assert world.get(f"/v1/evidence/{original['id']}/content").content.startswith(b"Call +1 415")
    assert b"Jane" not in world.get(f"/v1/evidence/{derived['id']}/content").content
    wrong_case = world.post(f"/v1/cases/{uuid.uuid4()}/evidence/{original['id']}/redact", {})
    assert wrong_case.status_code == 404
    transcript = world.upload(
        b"hello acme", source_type="AI_TRANSCRIPT", origin="ORIGINAL", media_type="text/plain"
    ).json()
    rev = world.post(
        f"/v1/evidence/{transcript['id']}/transcript-revisions",
        {"corrected_text": "hello ACME", "reason": "asr error"},
    )
    assert rev.status_code == 201 and rev.json()["supersedes_id"] == transcript["id"]
    versions = world.get(f"/v1/evidence/{rev.json()['id']}/versions").json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert world.get(f"/v1/evidence/{transcript['id']}/content").content == b"hello acme"
    again = world.post(
        f"/v1/evidence/{transcript['id']}/transcript-revisions",
        {"corrected_text": "z", "reason": "r"},
    )
    assert again.status_code == 409


def test_deletion_request_legal_hold_and_withdrawal(world: World) -> None:
    art = world.upload(b"keep me").json()
    held = world.post(f"/v1/evidence/{art['id']}/legal-hold", {"hold": True, "reason": "dispute"})
    assert held.status_code == 200 and held.json()["legal_hold"] is True
    denied = world.post(f"/v1/evidence/{art['id']}/deletion-requests", {"reason": "please delete"})
    body = denied.json()
    assert denied.status_code == 200 and body["status"] == "DENIED_LEGAL_HOLD"
    assert body["retained"][0]["reason"] == "LEGAL_HOLD" and body["limits"]
    assert world.get(f"/v1/evidence/{art['id']}/content").content == b"keep me"
    world.post(f"/v1/evidence/{art['id']}/legal-hold", {"hold": False, "reason": "settled"})
    done = world.post(
        f"/v1/evidence/{art['id']}/deletion-requests", {"reason": "please delete"}
    ).json()
    assert done["status"] == "COMPLETED" and done["deleted"] == [art["id"]]
    gone = world.get(f"/v1/evidence/{art['id']}/content")
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "ARTIFACT_DELETED"
    meta = world.get(f"/v1/evidence/{art['id']}").json()
    assert meta["deleted_at"] and meta["content_available"] is False and meta["sha256"]
    other = world.upload(b"another").json()
    withdrawn = world.post("/v1/me/withdrawal", {"reason": "stop"}).json()
    assert withdrawn["status"] == "COMPLETED" and withdrawn["artifacts_revoked"] >= 1
    assert world.get(f"/v1/evidence/{other['id']}/content").status_code == 403
    assert world.upload(b"after withdrawal").status_code == 403


def test_sharing_authorization_change_is_audited(world: World) -> None:
    art = world.upload(b"x", sharing_authorization="NOT_EVALUATED").json()
    resp = world.post(
        f"/v1/evidence/{art['id']}/authorization",
        {"field": "sharing_authorization", "state": "PERMITTED"},
    )
    assert resp.json()["sharing_authorization"] == "PERMITTED"
    bad = world.post(
        f"/v1/evidence/{art['id']}/authorization", {"field": "sha256", "state": "PERMITTED"}
    )
    assert bad.status_code == 422


def test_data_export_endpoint(world: World) -> None:
    data = world.get("/v1/me/data-export").json()
    assert data["user"]["email"] == world.user.email
    assert any(c["id"] == world.case_id for c in data["cases"])
    assert data["deletion_limits"] and "storage_key" not in str(data)


def _run_export_job(world: World) -> None:
    load_handler_modules()
    assert run_one(world.factory, ["case.export"]) is True


def test_export_flow_status_download_and_token(world: World, app_settings: Settings) -> None:
    resp = world.post(
        f"/v1/cases/{world.case_id}/exports", {"artifact_ids": [world.artifact_id]},
        headers={"Idempotency-Key": "exp-1"},
    )  # fmt: skip
    assert resp.status_code == 202 and resp.json()["status"] == "QUEUED"
    export_id = resp.json()["id"]
    replay = world.client.post(
        f"/v1/cases/{world.case_id}/exports", json={"artifact_ids": [world.artifact_id]},
        headers={**world.user.headers, "Idempotency-Key": "exp-1"},
    )  # fmt: skip
    assert replay.status_code == 200 and replay.json()["id"] == export_id
    base = f"/v1/cases/{world.case_id}/exports/{export_id}"
    assert world.get(base + "/download").status_code == 409  # not built yet
    _run_export_job(world)
    status = world.get(base).json()
    assert status["status"] == "COMPLETED" and status["downloadable"] and status["export_digest"]
    zipped = world.get(base + "/download")
    assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"
    assert (
        f"case_{world.case_id}_{status['snapshot_id']}.zip" in zipped.headers["content-disposition"]
    )
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert "manifest.json" in names and any(n.startswith("attachments/") for n in names)
    token = world.post(base + "/download-token").json()["token"]
    anonymous = world.client.get("/v1/exports/download", params={"token": token})
    assert anonymous.status_code == 200 and anonymous.content == zipped.content
    assert (
        world.client.get("/v1/exports/download", params={"token": token + "x"}).status_code == 401
    )
    expired, _ = create_download_token(
        app_settings,
        world.user.tenant_id,
        uuid.UUID(export_id),
        ttl_seconds=1,
        now=utcnow().replace(year=2020),
    )
    assert world.client.get("/v1/exports/download", params={"token": expired}).status_code == 401


def test_review_items_endpoint_lists_open_conflicts(world: World) -> None:
    with world.factory() as session:
        line_id = world.line_id
        a = seed_incident(session, world.user.tenant_id, line_id, claimed="Acme Lending")
        b = seed_incident(session, world.user.tenant_id, line_id, claimed="Bright Solar")
        from callshield.casebuilder import run_incident_pipeline

        run_incident_pipeline(session, world.user.tenant_id, a.id)
        run_incident_pipeline(session, world.user.tenant_id, b.id)
        session.commit()
    items = world.get("/v1/case-review-items").json()["items"]
    assert len(items) == 1 and items[0]["reason"] == "same_cli_different_claim"
    resolved = world.post(
        f"/v1/cases/{world.case_id}/incidents:link", {"incident_id": items[0]["incident_id"]}
    )
    assert resolved.status_code == 200
    assert world.get("/v1/case-review-items").json()["items"] == []


# --------------------------------------------------------------- cross-tenant denial


def _cross_tenant_calls(w: World) -> list[tuple[str, str, dict[str, Any] | None]]:
    case, art, inc = w.case_id, w.artifact_id, str(w.incidents[1])
    return [
        ("get", f"/v1/cases/{case}", None),
        ("get", f"/v1/cases/{case}/timeline", None),
        ("post", f"/v1/cases/{case}/incidents:link", {"incident_id": inc}),
        ("post", f"/v1/cases/{case}/incidents:unlink", {"incident_id": inc, "reason": "x"}),
        ("post", f"/v1/cases/{case}/incidents/{inc}/reclassify", {"label": "INFORMATIONAL"}),
        ("post", f"/v1/cases/{case}/transition", {"to_state": "INTAKE"}),
        ("post", f"/v1/cases/{case}/attribution-review", {"status": "DISPUTED", "basis": "x"}),
        ("post", f"/v1/cases/{case}/exports", {}),
        ("post", f"/v1/cases/{case}/imports/user-attested", {"accounts": [{"narrative": "n"}]}),
        ("post", f"/v1/cases/{case}/evidence", {
            "source_type": "SCREENSHOT", "content_base64": B64(b"x").decode(),
            "processing_authorization": "PERMITTED", "sharing_authorization": "PERMITTED"}),
        ("post", f"/v1/cases/{case}/evidence/{art}/redact", {}),
        ("get", f"/v1/evidence/{art}", None),
        ("get", f"/v1/evidence/{art}/content", None),
        ("get", f"/v1/evidence/{art}/versions", None),
        ("post", f"/v1/evidence/{art}/transcript-revisions", {"corrected_text": "x", "reason": "y"}),  # noqa: E501
        ("post", f"/v1/evidence/{art}/deletion-requests", {"reason": "x"}),
        ("post", f"/v1/evidence/{art}/legal-hold", {"hold": True, "reason": "x"}),
        ("post", f"/v1/evidence/{art}/authorization",
         {"field": "sharing_authorization", "state": "PERMITTED"}),
    ]  # fmt: skip


def test_every_endpoint_denies_cross_tenant_access_with_404(
    world: World, make_tenant_user: TenantUserFactory
) -> None:
    intruder = make_tenant_user("Intruder")
    export = world.post(f"/v1/cases/{world.case_id}/exports", {}).json()["id"]
    _run_export_job(world)
    calls = [
        *_cross_tenant_calls(world),
        ("get", f"/v1/cases/{world.case_id}/exports/{export}", None),
        ("get", f"/v1/cases/{world.case_id}/exports/{export}/download", None),
        ("post", f"/v1/cases/{world.case_id}/exports/{export}/download-token", None),
        ("post", "/v1/cases", {"protected_line_id": str(world.line_id), "title": "steal"}),
    ]
    for method, path, body in calls:
        resp = getattr(world.client, method)(
            path, headers=intruder.headers, **({"json": body} if method == "post" else {})
        )
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        text = resp.text
        for secret in (world.case_id, world.artifact_id, "first evidence", str(world.line_id)):
            assert secret not in text, (path, secret)
    # nothing leaked into lists either
    assert world.client.get("/v1/cases", headers=intruder.headers).json()["total"] == 0
    assert world.client.get("/v1/case-review-items", headers=intruder.headers).json()["items"] == []
    mine = world.client.get("/v1/me/data-export", headers=intruder.headers).json()
    assert mine["cases"] == [] and mine["evidence_artifacts"] == [] and mine["incidents"] == []
    # the owner is untouched by the intruder's attempts
    assert world.get(f"/v1/cases/{world.case_id}").json()["case"]["state"] == "OPENED"
    assert world.get(f"/v1/evidence/{world.artifact_id}").json()["legal_hold"] is False
    assert world.get(f"/v1/evidence/{world.artifact_id}/content").content == b"first evidence"


def test_download_token_for_another_tenants_export_cannot_be_minted(
    world: World,
    make_tenant_user: TenantUserFactory,
    app_settings: Settings,
) -> None:
    intruder = make_tenant_user("Intruder 2")
    export = world.post(f"/v1/cases/{world.case_id}/exports", {}).json()["id"]
    _run_export_job(world)
    # A token minted for the intruder's tenant id but A's export id resolves to nothing.
    forged, _ = create_download_token(app_settings, intruder.tenant_id, uuid.UUID(export))
    resp = world.client.get("/v1/exports/download", params={"token": forged})
    assert resp.status_code == 404


def test_case_creation_requires_line_in_own_tenant(
    world: World, make_tenant_user: TenantUserFactory
) -> None:
    intruder = make_tenant_user("Intruder 3")
    resp = world.client.post(
        "/v1/cases", json={"protected_line_id": str(world.line_id), "title": "x"},
        headers=intruder.headers,
    )  # fmt: skip
    assert resp.status_code == 404
    with world.factory() as session:
        assert session.query(Case).filter(Case.tenant_id == intruder.tenant_id).count() == 0
