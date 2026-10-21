"""Transactional submissions jobs and idempotency

Revision ID: b06_submit_queue
Revises: b05_product_policy
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b06_submit_queue"
down_revision = "b05_product_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_submissions",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("form_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consent_to_checks", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "consent_to_checks", name=op.f("ck_application_submissions_consent_required")
        ),
        sa.CheckConstraint(
            "input_revision > 0", name=op.f("ck_application_submissions_input_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_submissions_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_submissions")),
        sa.UniqueConstraint("application_id", "id", name="uq_submissions_application_id"),
        sa.UniqueConstraint(
            "application_id", "input_revision", name="uq_submissions_input_revision"
        ),
    )
    op.create_table(
        "submit_idempotency",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column(
            "operation",
            sa.Enum(
                "submit", name="idempotency_operation", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at >= created_at + interval '24 hours'",
            name=op.f("ck_submit_idempotency_retention"),
        ),
        sa.CheckConstraint(
            "response_status = 202", name=op.f("ck_submit_idempotency_successful_status")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_submit_idempotency_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_submit_idempotency_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submit_idempotency")),
        sa.UniqueConstraint(
            "user_id",
            "application_id",
            "operation",
            "key",
            name=op.f("uq_submit_idempotency_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_submit_idempotency_expires_at"), "submit_idempotency", ["expires_at"], unique=False
    )
    op.create_table(
        "pipeline_jobs",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "application_pipeline", name="job_kind", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "cancelled",
                "failed",
                name="job_state",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (state <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_pipeline_jobs_lease_state"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_pipeline_jobs_attempts_nonnegative")),
        sa.ForeignKeyConstraint(
            ["application_id", "submission_id"],
            ["application_submissions.application_id", "application_submissions.id"],
            name="fk_pipeline_jobs_submission",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_pipeline_jobs_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_jobs")),
        sa.UniqueConstraint("submission_id", "kind", name=op.f("uq_pipeline_jobs_submission_id")),
    )
    op.create_index(
        "ix_pipeline_jobs_application", "pipeline_jobs", ["application_id"], unique=False
    )
    op.create_index(
        "ix_pipeline_jobs_lease", "pipeline_jobs", ["state", "lease_expires_at"], unique=False
    )
    op.create_index(
        "ix_pipeline_jobs_ready", "pipeline_jobs", ["state", "available_at", "id"], unique=False
    )
    op.add_column("applications", sa.Column("input_revision", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_applications_input_revision"),
        "applications",
        "input_revision > 0 AND input_revision <= revision",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_applications_input_revision"), "applications", type_="check")
    op.drop_column("applications", "input_revision")
    op.drop_index("ix_pipeline_jobs_ready", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_lease", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_application", table_name="pipeline_jobs")
    op.drop_table("pipeline_jobs")
    op.drop_index(op.f("ix_submit_idempotency_expires_at"), table_name="submit_idempotency")
    op.drop_table("submit_idempotency")
    op.drop_table("application_submissions")
