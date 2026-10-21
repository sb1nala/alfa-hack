"""Persistent authentication sessions and CSRF contexts.

Revision ID: b04_auth
Revises: b03_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b04_auth"
down_revision: str | None = "b03_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "client",
                "underwriter",
                name="session_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_auth_sessions_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_auth_sessions_revocation_time"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index("ix_auth_sessions_user_revoked", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "csrf_contexts",
        sa.Column("cookie_digest", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(cookie_digest) = 64", name=op.f("ck_csrf_contexts_digest_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_csrf_contexts_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_csrf_contexts_session_id_auth_sessions"),
        ),
        sa.PrimaryKeyConstraint("cookie_digest", name=op.f("pk_csrf_contexts")),
    )
    op.create_index("ix_csrf_contexts_session_id", "csrf_contexts", ["session_id"])
    op.create_index("ix_csrf_contexts_expires_at", "csrf_contexts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("csrf_contexts")
    op.drop_table("auth_sessions")
