"""enforce-core extension tables (dossier snapshots, exports, data-subject requests, reviews)

Revision ID: 0040
Revises: 0030
Create Date: 2026-10-27 14:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

TENANT_TABLES = (
    "case_dossier_snapshots",
    "case_exports",
    "data_subject_requests",
    "incident_reclassifications",
    "case_review_items",
)
_TENANT_EXPR = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({_TENANT_EXPR}) WITH CHECK ({_TENANT_EXPR})"
        )


def upgrade() -> None:
    op.create_table(
        "case_dossier_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("meta", JSONType, nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_dossier_snapshots_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_dossier_snapshots_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_dossier_snapshots")),
        sa.UniqueConstraint(
            "case_id", "snapshot_no", name=op.f("uq_case_dossier_snapshots_case_id")
        ),
    )
    op.create_index(
        op.f("ix_case_dossier_snapshots_case_id"),
        "case_dossier_snapshots",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_dossier_snapshots_tenant_id"),
        "case_dossier_snapshots",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "case_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("selection", JSONType, nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("zip_sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_length", sa.BigInteger(), nullable=True),
        sa.Column("export_digest", sa.String(length=64), nullable=True),
        sa.Column("manifest_summary", JSONType, nullable=False),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_exports_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["case_dossier_snapshots.id"],
            name=op.f("fk_case_exports_snapshot_id_case_dossier_snapshots"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_exports_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_exports")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name=op.f("uq_case_exports_tenant_id")),
    )
    op.create_index(op.f("ix_case_exports_case_id"), "case_exports", ["case_id"], unique=False)
    op.create_index(op.f("ix_case_exports_tenant_id"), "case_exports", ["tenant_id"], unique=False)
    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scope", JSONType, nullable=False),
        sa.Column("result", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_data_subject_requests_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_data_subject_requests_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_subject_requests")),
    )
    op.create_index(
        op.f("ix_data_subject_requests_tenant_id"),
        "data_subject_requests",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "incident_reclassifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("previous_label", sa.String(length=32), nullable=False),
        sa.Column("new_label", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_incident_reclassifications_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_reclassifications_incident_id_incidents"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_incident_reclassifications_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_reclassifications")),
    )
    op.create_index(
        op.f("ix_incident_reclassifications_case_id"),
        "incident_reclassifications",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incident_reclassifications_incident_id"),
        "incident_reclassifications",
        ["incident_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incident_reclassifications_tenant_id"),
        "incident_reclassifications",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "case_review_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("candidate_case_ids", JSONType, nullable=False),
        sa.Column("match_ruleset_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolution", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_case_review_items_incident_id_incidents"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_review_items_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_review_items")),
    )
    op.create_index(
        op.f("ix_case_review_items_incident_id"), "case_review_items", ["incident_id"], unique=False
    )
    op.create_index(
        op.f("ix_case_review_items_tenant_id"), "case_review_items", ["tenant_id"], unique=False
    )

    _enable_rls()


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
