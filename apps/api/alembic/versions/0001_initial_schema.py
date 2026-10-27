"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-27 13:39:09.960096
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Tables carrying tenant_id (everything tenant-owned). Fixed list: migrations must
# not depend on the live model registry.
TENANT_TABLES = (
    "audit_events",
    "call_sessions",
    "caller_observations",
    "campaign_links",
    "candidate_campaigns",
    "case_assessments",
    "case_audit_events",
    "case_incidents",
    "cases",
    "classifications",
    "complaint_drafts",
    "consent_records",
    "contact_attempts",
    "contact_authorizations",
    "contact_destinations",
    "evidence_artifacts",
    "filing_drafts",
    "filing_submissions",
    "incidents",
    "ivr_paths",
    "jobs",
    "ledger_events",
    "opt_out_events",
    "organization_claims",
    "protected_lines",
    "resolution_cases",
    "routing_rules",
    "submission_approvals",
    "trusted_contacts",
    "users",
)

_TENANT_EXPR = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
# users must be readable pre-auth (login lookup by email); the app binds the tenant
# right after. Run the app as a non-owner role so RLS applies to it.
_USERS_EXPR = "nullif(current_setting('app.tenant_id', true), '') IS NULL OR " + _TENANT_EXPR


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _enable_rls() -> None:
    for table in TENANT_TABLES:
        expr = _USERS_EXPR if table == "users" else _TENANT_EXPR
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")


def _ledger_immutability() -> None:
    op.execute(
        """
        CREATE FUNCTION ledger_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER ledger_events_no_update_delete "
        "BEFORE UPDATE OR DELETE ON ledger_events "
        "FOR EACH ROW EXECUTE FUNCTION ledger_events_block_mutation()"
    )
    op.execute(
        "CREATE TRIGGER ledger_events_no_truncate BEFORE TRUNCATE ON ledger_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION ledger_events_block_mutation()"
    )


def upgrade() -> None:
    op.create_table(
        "destination_registry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agency", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("capability_evidence", sa.Text(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_requirements", sa.Text(), nullable=False),
        sa.Column("supported_issue_types", JSONType, nullable=False),
        sa.Column("supported_fields", JSONType, nullable=False),
        sa.Column("attachment_constraints", JSONType, nullable=False),
        sa.Column("retention", sa.Text(), nullable=False),
        sa.Column("sunset_procedure", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destination_registry")),
    )
    with op.batch_alter_table("destination_registry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_destination_registry_agency"), ["agency"], unique=False
        )

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("hosting_mode", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction_policy_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("data", JSONType, nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("immutable_ref", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "candidate_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("features_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_candidate_campaigns_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_campaigns")),
    )
    with op.batch_alter_table("candidate_campaigns", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_candidate_campaigns_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_jobs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name=op.f("uq_jobs_tenant_id")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_state_run_after", ["state", "run_after"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "ledger_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_ledger_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_events")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_ledger_events_tenant_id")
        ),
    )
    with op.batch_alter_table("ledger_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ledger_events_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_ledger_events_tenant_topic_created",
            ["tenant_id", "topic", "created_at"],
            unique=False,
        )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("consent_preferences", JSONType, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("mfa_enrolled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_users_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "protected_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("e164", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("screening_enabled", sa.Boolean(), nullable=False),
        sa.Column("routing_config", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_protected_lines_owner_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_protected_lines_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_protected_lines")),
    )
    with op.batch_alter_table("protected_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_protected_lines_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_call_id", sa.String(length=200), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["protected_lines.id"],
            name=op.f("fk_call_sessions_line_id_protected_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_call_sessions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_sessions")),
        sa.UniqueConstraint(
            "tenant_id", "provider", "provider_call_id", name=op.f("uq_call_sessions_tenant_id")
        ),
    )
    with op.batch_alter_table("call_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_call_sessions_line_id"), ["line_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_call_sessions_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("protected_line_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("attribution_status", sa.String(length=32), nullable=False),
        sa.Column("recording_authorization", sa.String(length=32), nullable=False),
        sa.Column("legal_assessment", sa.String(length=32), nullable=False),
        sa.Column("case_resolution", sa.String(length=32), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["protected_line_id"],
            ["protected_lines.id"],
            name=op.f("fk_cases_protected_line_id_protected_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_cases_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name=op.f("uq_cases_tenant_id")),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_cases_protected_line_id"), ["protected_line_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_cases_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "routing_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("matcher", JSONType, nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["protected_lines.id"],
            name=op.f("fk_routing_rules_line_id_protected_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_routing_rules_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_rules")),
    )
    with op.batch_alter_table("routing_rules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_routing_rules_line_id"), ["line_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_routing_rules_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "trusted_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("e164", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["protected_lines.id"],
            name=op.f("fk_trusted_contacts_line_id_protected_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_trusted_contacts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trusted_contacts")),
        sa.UniqueConstraint(
            "tenant_id", "line_id", "e164", name=op.f("uq_trusted_contacts_tenant_id")
        ),
    )
    with op.batch_alter_table("trusted_contacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_trusted_contacts_line_id"), ["line_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_trusted_contacts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "caller_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("displayed_caller_id", sa.String(length=64), nullable=True),
        sa.Column("attestation_metadata", JSONType, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["call_sessions.id"],
            name=op.f("fk_caller_observations_session_id_call_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["caller_observations.id"],
            name=op.f("fk_caller_observations_supersedes_id_caller_observations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_caller_observations_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_caller_observations")),
    )
    with op.batch_alter_table("caller_observations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_caller_observations_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_caller_observations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "case_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("ruleset_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("issue_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("supporting_event_ids", JSONType, nullable=False),
        sa.Column("missing_requirements", JSONType, nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_assessments_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_assessments_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_assessments")),
    )
    with op.batch_alter_table("case_assessments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_assessments_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_case_assessments_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "case_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_data", JSONType, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_audit_events_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_audit_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_audit_events")),
    )
    with op.batch_alter_table("case_audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_audit_events_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_case_audit_events_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "classifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("reviewed_status", sa.String(length=32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id"], ["call_sessions.id"], name=op.f("fk_classifications_call_id_call_sessions")
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["classifications.id"],
            name=op.f("fk_classifications_supersedes_id_classifications"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_classifications_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classifications")),
    )
    with op.batch_alter_table("classifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_classifications_call_id"), ["call_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_classifications_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "consent_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("processing_mode", sa.String(length=32), nullable=False),
        sa.Column("legal_basis_config", JSONType, nullable=False),
        sa.Column("authorized", sa.Boolean(), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["call_sessions.id"],
            name=op.f("fk_consent_records_session_id_call_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_consent_records_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_consent_records_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_records")),
    )
    with op.batch_alter_table("consent_records", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_consent_records_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_consent_records_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("agency", sa.String(length=32), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft_json", JSONType, nullable=False),
        sa.Column("evidence_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_filing_drafts_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_drafts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_drafts")),
    )
    with op.batch_alter_table("filing_drafts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_filing_drafts_case_id"), ["case_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_filing_drafts_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_session_id", sa.Uuid(), nullable=True),
        sa.Column("line_id", sa.Uuid(), nullable=True),
        sa.Column("displayed_caller_id", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_session_id"],
            ["call_sessions.id"],
            name=op.f("fk_incidents_call_session_id_call_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["line_id"], ["protected_lines.id"], name=op.f("fk_incidents_line_id_protected_lines")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_incidents_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_incidents_call_session_id"), ["call_session_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_incidents_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "organization_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("claimed_name", sa.String(length=300), nullable=False),
        sa.Column("statement_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["call_sessions.id"],
            name=op.f("fk_organization_claims_session_id_call_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["organization_claims.id"],
            name=op.f("fk_organization_claims_supersedes_id_organization_claims"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_organization_claims_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_claims")),
    )
    with op.batch_alter_table("organization_claims", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_organization_claims_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_organization_claims_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "resolution_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("protected_line_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_entity", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("policy_region", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enforce_case_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enforce_case_id"],
            ["cases.id"],
            name=op.f("fk_resolution_cases_enforce_case_id_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_resolution_cases_owner_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["protected_line_id"],
            ["protected_lines.id"],
            name=op.f("fk_resolution_cases_protected_line_id_protected_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_resolution_cases_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_cases")),
    )
    with op.batch_alter_table("resolution_cases", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_resolution_cases_enforce_case_id"), ["enforce_case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_resolution_cases_protected_line_id"), ["protected_line_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_resolution_cases_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "campaign_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["candidate_campaigns.id"],
            name=op.f("fk_campaign_links_campaign_id_candidate_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f("fk_campaign_links_incident_id_incidents")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_campaign_links_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_links")),
    )
    with op.batch_alter_table("campaign_links", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_campaign_links_campaign_id"), ["campaign_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_campaign_links_incident_id"), ["incident_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_campaign_links_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "case_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("linkage_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_basis", JSONType, nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_incidents_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f("fk_case_incidents_incident_id_incidents")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_case_incidents_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_incidents")),
    )
    with op.batch_alter_table("case_incidents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_case_incidents_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_case_incidents_incident_id"), ["incident_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_case_incidents_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "complaint_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("body", JSONType, nullable=False),
        sa.Column("user_review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_complaint_drafts_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_complaint_drafts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_complaint_drafts")),
    )
    with op.batch_alter_table("complaint_drafts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_complaint_drafts_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_complaint_drafts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "contact_destinations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("source_url_or_evidence", JSONType, nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_contact_destinations_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["contact_destinations.id"],
            name=op.f("fk_contact_destinations_supersedes_id_contact_destinations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_contact_destinations_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            name=op.f("fk_contact_destinations_verified_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_destinations")),
    )
    with op.batch_alter_table("contact_destinations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contact_destinations_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_contact_destinations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_case_id", sa.Uuid(), nullable=True),
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=48), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_length", sa.BigInteger(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collection_method", sa.String(length=100), nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("processing_authorization", sa.String(length=32), nullable=False),
        sa.Column("sharing_authorization", sa.String(length=32), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=True),
        sa.Column("redaction_state", sa.String(length=32), nullable=False),
        sa.Column("derived_from_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provenance", JSONType, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_evidence_artifacts_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["consent_id"],
            ["consent_records.id"],
            name=op.f("fk_evidence_artifacts_consent_id_consent_records"),
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_id"],
            ["evidence_artifacts.id"],
            name=op.f("fk_evidence_artifacts_derived_from_id_evidence_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_evidence_artifacts_incident_id_incidents"),
        ),
        sa.ForeignKeyConstraint(
            ["resolution_case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_evidence_artifacts_resolution_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["evidence_artifacts.id"],
            name=op.f("fk_evidence_artifacts_supersedes_id_evidence_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_evidence_artifacts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_artifacts")),
    )
    with op.batch_alter_table("evidence_artifacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_evidence_artifacts_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_artifacts_resolution_case_id"),
            ["resolution_case_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_artifacts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "submission_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("approved_payload_digest", sa.String(length=64), nullable=False),
        sa.Column("attestation_version", sa.String(length=64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["filing_drafts.id"],
            name=op.f("fk_submission_approvals_draft_id_filing_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_submission_approvals_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_submission_approvals_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_approvals")),
    )
    with op.batch_alter_table("submission_approvals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_submission_approvals_draft_id"), ["draft_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_submission_approvals_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "contact_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=48), nullable=False),
        sa.Column("authorization_basis", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_contact_authorizations_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["contact_destinations.id"],
            name=op.f("fk_contact_authorizations_destination_id_contact_destinations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_contact_authorizations_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_contact_authorizations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_authorizations")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_contact_authorizations_tenant_id")
        ),
    )
    with op.batch_alter_table("contact_authorizations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contact_authorizations_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_contact_authorizations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "filing_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transport_response_ref", sa.String(length=500), nullable=True),
        sa.Column("agency_receipt", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["submission_approvals.id"],
            name=op.f("fk_filing_submissions_approval_id_submission_approvals"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["filing_drafts.id"],
            name=op.f("fk_filing_submissions_draft_id_filing_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_filing_submissions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_submissions")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_filing_submissions_idempotency_key")),
    )
    with op.batch_alter_table("filing_submissions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_filing_submissions_draft_id"), ["draft_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_filing_submissions_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "ivr_paths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("choices", JSONType, nullable=False),
        sa.Column("user_approved", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("fallback", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["contact_destinations.id"],
            name=op.f("fk_ivr_paths_destination_id_contact_destinations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_ivr_paths_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ivr_paths")),
    )
    with op.batch_alter_table("ivr_paths", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ivr_paths_destination_id"), ["destination_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ivr_paths_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "contact_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("user_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("call_session_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ivr_route", JSONType, nullable=False),
        sa.Column("agent_claims", JSONType, nullable=False),
        sa.Column("policy_state", JSONType, nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_session_id"],
            ["call_sessions.id"],
            name=op.f("fk_contact_attempts_call_session_id_call_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_contact_attempts_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["contact_destinations.id"],
            name=op.f("fk_contact_attempts_destination_id_contact_destinations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_contact_attempts_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_authorization_id"],
            ["contact_authorizations.id"],
            name=op.f("fk_contact_attempts_user_authorization_id_contact_authorizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_attempts")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_contact_attempts_tenant_id")
        ),
    )
    with op.batch_alter_table("contact_attempts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_contact_attempts_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_contact_attempts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "opt_out_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=True),
        sa.Column("request_text_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["contact_attempts.id"],
            name=op.f("fk_opt_out_events_attempt_id_contact_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["resolution_cases.id"],
            name=op.f("fk_opt_out_events_case_id_resolution_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["opt_out_events.id"],
            name=op.f("fk_opt_out_events_supersedes_id_opt_out_events"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_opt_out_events_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opt_out_events")),
    )
    with op.batch_alter_table("opt_out_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_opt_out_events_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_opt_out_events_tenant_id"), ["tenant_id"], unique=False
        )
    if _is_postgres():
        _enable_rls()
        _ledger_immutability()


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP TRIGGER IF EXISTS ledger_events_no_truncate ON ledger_events")
        op.execute("DROP TRIGGER IF EXISTS ledger_events_no_update_delete ON ledger_events")
        op.execute("DROP FUNCTION IF EXISTS ledger_events_block_mutation()")
    with op.batch_alter_table("opt_out_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_opt_out_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_opt_out_events_case_id"))

    op.drop_table("opt_out_events")
    with op.batch_alter_table("contact_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_attempts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_contact_attempts_case_id"))

    op.drop_table("contact_attempts")
    with op.batch_alter_table("ivr_paths", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ivr_paths_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_ivr_paths_destination_id"))

    op.drop_table("ivr_paths")
    with op.batch_alter_table("filing_submissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_submissions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_submissions_draft_id"))

    op.drop_table("filing_submissions")
    with op.batch_alter_table("contact_authorizations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_authorizations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_contact_authorizations_case_id"))

    op.drop_table("contact_authorizations")
    with op.batch_alter_table("submission_approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_submission_approvals_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_submission_approvals_draft_id"))

    op.drop_table("submission_approvals")
    with op.batch_alter_table("evidence_artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evidence_artifacts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_artifacts_resolution_case_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_artifacts_case_id"))

    op.drop_table("evidence_artifacts")
    with op.batch_alter_table("contact_destinations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_destinations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_contact_destinations_case_id"))

    op.drop_table("contact_destinations")
    with op.batch_alter_table("complaint_drafts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_complaint_drafts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_complaint_drafts_case_id"))

    op.drop_table("complaint_drafts")
    with op.batch_alter_table("case_incidents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_incidents_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_case_incidents_incident_id"))
        batch_op.drop_index(batch_op.f("ix_case_incidents_case_id"))

    op.drop_table("case_incidents")
    with op.batch_alter_table("campaign_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_campaign_links_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_campaign_links_incident_id"))
        batch_op.drop_index(batch_op.f("ix_campaign_links_campaign_id"))

    op.drop_table("campaign_links")
    with op.batch_alter_table("resolution_cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resolution_cases_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_resolution_cases_protected_line_id"))
        batch_op.drop_index(batch_op.f("ix_resolution_cases_enforce_case_id"))

    op.drop_table("resolution_cases")
    with op.batch_alter_table("organization_claims", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_organization_claims_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_organization_claims_session_id"))

    op.drop_table("organization_claims")
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_incidents_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_incidents_call_session_id"))

    op.drop_table("incidents")
    with op.batch_alter_table("filing_drafts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filing_drafts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_filing_drafts_case_id"))

    op.drop_table("filing_drafts")
    with op.batch_alter_table("consent_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_consent_records_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_consent_records_session_id"))

    op.drop_table("consent_records")
    with op.batch_alter_table("classifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_classifications_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_classifications_call_id"))

    op.drop_table("classifications")
    with op.batch_alter_table("case_audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_audit_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_case_audit_events_case_id"))

    op.drop_table("case_audit_events")
    with op.batch_alter_table("case_assessments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_case_assessments_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_case_assessments_case_id"))

    op.drop_table("case_assessments")
    with op.batch_alter_table("caller_observations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_caller_observations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_caller_observations_session_id"))

    op.drop_table("caller_observations")
    with op.batch_alter_table("trusted_contacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_trusted_contacts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_trusted_contacts_line_id"))

    op.drop_table("trusted_contacts")
    with op.batch_alter_table("routing_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_routing_rules_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_routing_rules_line_id"))

    op.drop_table("routing_rules")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_cases_protected_line_id"))

    op.drop_table("cases")
    with op.batch_alter_table("call_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_call_sessions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_call_sessions_line_id"))

    op.drop_table("call_sessions")
    with op.batch_alter_table("protected_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_protected_lines_tenant_id"))

    op.drop_table("protected_lines")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_tenant_id"))

    op.drop_table("users")
    with op.batch_alter_table("ledger_events", schema=None) as batch_op:
        batch_op.drop_index("ix_ledger_events_tenant_topic_created")
        batch_op.drop_index(batch_op.f("ix_ledger_events_tenant_id"))

    op.drop_table("ledger_events")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_tenant_id"))
        batch_op.drop_index("ix_jobs_state_run_after")

    op.drop_table("jobs")
    with op.batch_alter_table("candidate_campaigns", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_campaigns_tenant_id"))

    op.drop_table("candidate_campaigns")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_tenant_id"))

    op.drop_table("audit_events")
    op.drop_table("tenants")
    with op.batch_alter_table("destination_registry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_destination_registry_agency"))

    op.drop_table("destination_registry")
