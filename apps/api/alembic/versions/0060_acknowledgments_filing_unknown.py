"""acknowledgments table and filing UNKNOWN user-resolution columns

Revision ID: 0060
Revises: 0050
Create Date: 2026-10-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import callshield.db.base

revision: str = "0060"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("acknowledgments",)
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
        "acknowledgments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("acknowledgment_version", sa.String(length=64), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("operator_id", sa.String(length=100), nullable=False),
        sa.Column(
            "item_ids", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("accepted_at", callshield.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_acknowledgments_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_acknowledgments_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_acknowledgments")),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "text_sha256", name=op.f("uq_acknowledgments_tenant_id")
        ),
    )
    with op.batch_alter_table("acknowledgments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_acknowledgments_tenant_id"), ["tenant_id"])
        batch_op.create_index(batch_op.f("ix_acknowledgments_user_id"), ["user_id"])

    with op.batch_alter_table("filing_submission_ext", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("unknown_since", callshield.db.base.UTCDateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("user_resolution", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "user_resolved_at", callshield.db.base.UTCDateTime(timezone=True), nullable=True
            )
        )

    _enable_rls()


def downgrade() -> None:
    with op.batch_alter_table("filing_submission_ext", schema=None) as batch_op:
        batch_op.drop_column("user_resolved_at")
        batch_op.drop_column("user_resolution")
        batch_op.drop_column("unknown_since")

    with op.batch_alter_table("acknowledgments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_acknowledgments_user_id"))
        batch_op.drop_index(batch_op.f("ix_acknowledgments_tenant_id"))

    op.drop_table("acknowledgments")
