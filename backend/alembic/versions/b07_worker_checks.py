"""Persistent checks and explicit B08 handoff.

Revision ID: b07_worker_checks
Revises: b06_submit_queue
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b07_worker_checks"
down_revision = "b06_submit_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_jobs",
        sa.Column("phase", sa.String(8), nullable=False, server_default=sa.text("'checks'")),
    )
    op.create_check_constraint("job_phase", "pipeline_jobs", "phase IN ('checks', 'decision')")
    op.add_column("pipeline_jobs", sa.Column("last_error_stage", sa.Text(), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("last_error_category", sa.Text(), nullable=True))
    op.create_index(
        "ix_pipeline_jobs_phase_ready", "pipeline_jobs", ["phase", "state", "available_at", "id"]
    )
    op.create_table(
        "submission_checks",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("egrul", postgresql.JSONB(), nullable=False),
        sa.Column("pb", postgresql.JSONB(), nullable=False),
        sa.Column("bki", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("submission_id", name="pk_submission_checks"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["application_submissions.id"],
            name="fk_submission_checks_submission_id_application_submissions",
        ),
    )


def downgrade() -> None:
    op.drop_table("submission_checks")
    op.drop_index("ix_pipeline_jobs_phase_ready", table_name="pipeline_jobs")
    op.drop_column("pipeline_jobs", "last_error_category")
    op.drop_column("pipeline_jobs", "last_error_stage")
    op.drop_constraint(op.f("ck_pipeline_jobs_job_phase"), "pipeline_jobs", type_="check")
    op.drop_column("pipeline_jobs", "phase")
