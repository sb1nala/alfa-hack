"""Freeze uploaded document types with every input version.

Revision ID: b11_submission_documents
Revises: b08_decision_result
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b11_submission_documents"
down_revision = "b08_decision_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_submissions",
        sa.Column(
            "document_types",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("application_submissions", "document_types")
