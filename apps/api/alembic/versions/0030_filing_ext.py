"""enforce-filing extension tables (assessment runs, filing meta, receipts)

Revision ID: 0030
Revises: 0001
Create Date: 2026-10-27 13:50:15.903893
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

TENANT_TABLES = (
    "assessment_runs",
    "filing_approval_details",
    "filing_draft_meta",
    "filing_receipts",
    "filing_submission_ext",
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
        "destination_registry_ext",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=True),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("manual_handoff", JSONType, nullable=False),
        sa.Column("notices", JSONType, nullable=False),
        sa.Column("seed_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["destination_registry.id"],
            name=op.f("fk_destination_registry_ext_destination_id_destination_registry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destination_registry_ext")),
        sa.UniqueConstraint(
            "destination_id", name=op.f("uq_destination_registry_ext_destination_id")
        ),
        sa.UniqueConstraint("slug", name=op.f("uq_destination_registry_ext_slug")),
    )
    op.create_table(
        "assessment_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("ruleset_version", sa.String(length=64), nullable=False),
        sa.Column("user_facts", JSONType, nullable=False),
        sa.Column("facts_digest", sa.String(length=64), nullable=False),
        sa.Column("results", JSONType, nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("ai_commentary", sa.Text(), nullable=True),
        sa.Column("ai_commentary_label", sa.String(length=32), nullable=True),
        sa.Column("ai_model_version", sa.String(length=100), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_assessment_runs_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_assessment_runs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_runs")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_assessment_runs_tenant_id")
        ),
    )
    with op.batch_alter_table("assessment_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assessment_runs_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_assessment_runs_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_approval_details",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("statement_ids", JSONType, nullable=False),
        sa.Column("displayed_items", JSONType, nullable=False),
        sa.Column("completed_fields", JSONType, nullable=False),
        sa.Column("sharing_confirmed", sa.Boolean(), nullable=False),
        sa.Column("destination_validated", sa.Boolean(), nullable=False),
        sa.Column("line_authorized", sa.Boolean(), nullable=False),
        sa.Column("evidence_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=48), nullable=False),
        sa.Column("authorization_basis", sa.String(length=200), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["submission_approvals.id"],
            name=op.f("fk_filing_approval_details_approval_id_submission_approvals"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["filing_drafts.id"],
            name=op.f("fk_filing_approval_details_draft_id_filing_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_approval_details_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_approval_details")),
        sa.UniqueConstraint("approval_id", name=op.f("uq_filing_approval_details_approval_id")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_filing_approval_details_tenant_id")
        ),
    )
    with op.batch_alter_table("filing_approval_details", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_filing_approval_details_draft_id"), ["draft_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_filing_approval_details_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_draft_meta",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("destination_slug", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("supersedes_draft_id", sa.Uuid(), nullable=True),
        sa.Column("supplements_submission_id", sa.Uuid(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("completeness", JSONType, nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("authorization_basis", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_filing_draft_meta_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_filing_draft_meta_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["destination_registry.id"],
            name=op.f("fk_filing_draft_meta_destination_id_destination_registry"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["filing_drafts.id"],
            name=op.f("fk_filing_draft_meta_draft_id_filing_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_draft_id"],
            ["filing_drafts.id"],
            name=op.f("fk_filing_draft_meta_supersedes_draft_id_filing_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["supplements_submission_id"],
            ["filing_submissions.id"],
            name=op.f("fk_filing_draft_meta_supplements_submission_id_filing_submissions"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_draft_meta_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_draft_meta")),
        sa.UniqueConstraint("draft_id", name=op.f("uq_filing_draft_meta_draft_id")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_filing_draft_meta_tenant_id")
        ),
    )
    with op.batch_alter_table("filing_draft_meta", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_filing_draft_meta_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_filing_draft_meta_lineage_id"), ["lineage_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_filing_draft_meta_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_kind", sa.String(length=32), nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("confirmation_number", sa.String(length=200), nullable=True),
        sa.Column("confirmation_source", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("endpoint", sa.String(length=1000), nullable=False),
        sa.Column("sanitized_response", JSONType, nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["filing_submissions.id"],
            name=op.f("fk_filing_receipts_submission_id_filing_submissions"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_receipts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_receipts")),
    )
    with op.batch_alter_table("filing_receipts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_filing_receipts_submission_id"), ["submission_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_filing_receipts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_submission_ext",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("agency", sa.String(length=32), nullable=False),
        sa.Column("destination_slug", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=300), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("supplements_submission_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("scope", sa.String(length=48), nullable=False),
        sa.Column("authorization_basis", sa.String(length=200), nullable=False),
        sa.Column("client_idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status_checks", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["submission_approvals.id"],
            name=op.f("fk_filing_submission_ext_approval_id_submission_approvals"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_filing_submission_ext_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["filing_submissions.id"],
            name=op.f("fk_filing_submission_ext_submission_id_filing_submissions"),
        ),
        sa.ForeignKeyConstraint(
            ["supplements_submission_id"],
            ["filing_submissions.id"],
            name=op.f("fk_filing_submission_ext_supplements_submission_id_filing_submissions"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_submission_ext_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_submission_ext")),
        sa.UniqueConstraint("approval_id", name=op.f("uq_filing_submission_ext_approval_id")),
        sa.UniqueConstraint("submission_id", name=op.f("uq_filing_submission_ext_submission_id")),
        sa.UniqueConstraint(
            "tenant_id", "dedupe_key", name=op.f("uq_filing_submission_ext_tenant_id")
        ),
    )
    with op.batch_alter_table("filing_submission_ext", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_filing_submission_ext_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_filing_submission_ext_tenant_id"), ["tenant_id"], unique=False
        )

    _enable_rls()


def downgrade() -> None:
    with op.batch_alter_table("filing_submission_ext", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_submission_ext_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_submission_ext_case_id"))

    op.drop_table("filing_submission_ext")
    with op.batch_alter_table("filing_receipts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_receipts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_receipts_submission_id"))

    op.drop_table("filing_receipts")
    with op.batch_alter_table("filing_draft_meta", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_draft_meta_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_draft_meta_lineage_id"))
        batch_op.drop_index(batch_op.f("ix_filing_draft_meta_case_id"))

    op.drop_table("filing_draft_meta")
    with op.batch_alter_table("filing_approval_details", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_approval_details_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_approval_details_draft_id"))

    op.drop_table("filing_approval_details")
    with op.batch_alter_table("assessment_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assessment_runs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_assessment_runs_case_id"))

    op.drop_table("assessment_runs")
    op.drop_table("destination_registry_ext")
