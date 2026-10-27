"""Evidence snapshot digest (filing/snapshot.py): latest-wins links, scoping, stability."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from callshield.core.hashing import canonical_json
from callshield.db.models.defend import ProtectedLine
from callshield.db.models.enforce import Case, CaseIncident
from callshield.domain.enums import EventOrigin
from callshield.domain.topics import Topic
from callshield.filing.snapshot import CaseSnapshot, load_case_snapshot
from callshield.ledger.service import append_event
from tests.conftest import TenantUser, TenantUserFactory
from tests.filing_helpers import T0, CaseBundle, add_artifact, add_incident, seed_case


@pytest.fixture
def world(make_tenant_user: TenantUserFactory, session: Session) -> tuple[TenantUser, CaseBundle]:
    user = make_tenant_user("Snap")
    return user, seed_case(session, user.tenant_id, user.user_id)


def _snap(session: Session, case_id: uuid.UUID) -> CaseSnapshot:
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return load_case_snapshot(session, case)


def test_digest_is_stable_and_pii_free(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    _user, bundle = world
    a, b = _snap(session, bundle.case_id), _snap(session, bundle.case_id)
    assert a.digest == b.digest and len(a.digest) == 64
    body = canonical_json(a.digest_body())
    assert "+1415555" not in body and "Acme" not in body
    assert len(a.incidents) == 2 and a.line is not None and len(a.ledger_event_ids) == 2


def test_new_material_changes_the_digest(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    base = _snap(session, bundle.case_id).digest
    add_incident(session, user.tenant_id, bundle, 7)
    session.commit()
    after_incident = _snap(session, bundle.case_id).digest
    assert after_incident != base
    add_artifact(session, user.tenant_id, bundle.case_id)
    after_artifact = _snap(session, bundle.case_id).digest
    assert after_artifact != after_incident
    line = session.get(ProtectedLine, bundle.line_id)
    assert line is not None
    line.verification_status = "REVOKED"
    session.commit()
    assert _snap(session, bundle.case_id).digest != after_artifact


def test_latest_link_wins_and_unlink_removes_without_deleting(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    target = bundle.incident_ids[0]
    session.add(CaseIncident(
        tenant_id=user.tenant_id, case_id=bundle.case_id, incident_id=target,
        linkage_type="USER_UNLINK", created_at=T0 + timedelta(days=100),
    ))  # fmt: skip
    session.commit()
    snap = _snap(session, bundle.case_id)
    assert target not in {i.incident_id for i in snap.incidents}
    assert len(snap.incidents) == 1
    session.add(CaseIncident(
        tenant_id=user.tenant_id, case_id=bundle.case_id, incident_id=target,
        linkage_type="USER_LINK", created_at=T0 + timedelta(days=101),
    ))  # fmt: skip
    session.commit()
    assert target in {i.incident_id for i in _snap(session, bundle.case_id).incidents}


def test_automatic_matches_are_suspected_not_confirmed(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    from callshield.domain.enums import LinkageType

    user, bundle = world
    add_incident(session, user.tenant_id, bundle, 8, linkage=LinkageType.AUTO_MATCH)
    session.commit()
    snap = _snap(session, bundle.case_id)
    assert sorted(i.confirmed for i in snap.incidents) == [False, True, True]


def test_filing_and_assessment_events_do_not_change_the_snapshot(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    base = _snap(session, bundle.case_id).digest
    for n, topic in enumerate(
        (
            Topic.FILING_DRAFTED,
            Topic.ASSESSMENT_COMPLETED,
            Topic.CASE_STATE_CHANGED,
            Topic.FILING_SUBMITTED,
        )
    ):
        append_event(session, user.tenant_id, topic, {"case_id": str(bundle.case_id)},
                     EventOrigin.USER_ACTION, f"own-{n}")  # fmt: skip
    session.commit()
    assert _snap(session, bundle.case_id).digest == base


def test_duplicate_provider_event_leaves_digest_unchanged(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    user, bundle = world
    payload = {"incident_id": str(bundle.incident_ids[0])}
    _ev, created = append_event(session, user.tenant_id, Topic.CALL_ENDED, payload,
                                EventOrigin.PROVIDER_WEBHOOK, "webhook-dup")  # fmt: skip
    session.commit()
    assert created
    once = _snap(session, bundle.case_id).digest
    for _ in range(3):
        _ev, created = append_event(session, user.tenant_id, Topic.CALL_ENDED, payload,
                                    EventOrigin.PROVIDER_WEBHOOK, "webhook-dup")  # fmt: skip
        assert not created
    session.commit()
    assert _snap(session, bundle.case_id).digest == once


def test_other_tenants_rows_never_leak_into_a_snapshot(
    session: Session, two_tenants: tuple[TenantUser, TenantUser]
) -> None:
    a, b = two_tenants
    bundle = seed_case(session, a.tenant_id, a.user_id)
    before = _snap(session, bundle.case_id)
    # tenant B writes rows that point at tenant A's case id
    foreign = add_artifact(session, b.tenant_id, bundle.case_id)
    session.add(CaseIncident(
        tenant_id=b.tenant_id, case_id=bundle.case_id, incident_id=bundle.incident_ids[0],
        linkage_type="USER_UNLINK", created_at=T0 + timedelta(days=500),
    ))  # fmt: skip
    session.commit()
    after = _snap(session, bundle.case_id)
    assert after.digest == before.digest
    assert foreign.id not in {x.artifact_id for x in after.artifacts}


def test_superseded_and_deleted_artifacts_are_excluded(
    session: Session, world: tuple[TenantUser, CaseBundle]
) -> None:
    from callshield.core.clock import utcnow

    user, bundle = world
    old = add_artifact(session, user.tenant_id, bundle.case_id)
    new = add_artifact(session, user.tenant_id, bundle.case_id)
    new.supersedes_id = old.id
    gone = add_artifact(session, user.tenant_id, bundle.case_id)
    gone.deleted_at = utcnow()
    session.commit()
    ids = {a.artifact_id for a in _snap(session, bundle.case_id).artifacts}
    assert ids == {new.id}
    assert json.loads(canonical_json({"ok": True})) == {"ok": True}
