"""Link automatic decisions to submissions and retain the complete risk result.

Revision ID: b08_decision_result
Revises: b07_worker_checks
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b08_decision_result"
down_revision = "b07_worker_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("submission_id", sa.Uuid(), nullable=True))
    op.add_column("decisions", sa.Column("risk_result", postgresql.JSONB(), nullable=True))
    op.create_unique_constraint("uq_decisions_submission_id", "decisions", ["submission_id"])
    op.create_foreign_key(
        "fk_decisions_submission",
        "decisions",
        "application_submissions",
        ["application_id", "submission_id"],
        ["application_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_decisions_submission", "decisions", type_="foreignkey")
    op.drop_constraint("uq_decisions_submission_id", "decisions", type_="unique")
    op.drop_column("decisions", "risk_result")
    op.drop_column("decisions", "submission_id")
