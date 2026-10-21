"""Store the risk owner's product policy without changing the public catalog.

Revision ID: b05_product_policy
Revises: b04_auth
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b05_product_policy"
down_revision = "b04_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "policy_snapshot")
