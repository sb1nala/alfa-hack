"""B03 initial business schema

Revision ID: b03_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b03_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("inn", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "legal_form",
            sa.Enum("OOO", "IP", name="legal_form", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("okved", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("inn ~ '^([0-9]{10}|[0-9]{12})$'", name=op.f("ck_companies_inn_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("inn", name=op.f("uq_companies_inn")),
    )
    op.create_table(
        "products",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_products_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("code", name=op.f("uq_products_code")),
    )
    op.create_table(
        "users",
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "client", "underwriter", name="role", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login", name=op.f("uq_users_login")),
    )
    op.create_table(
        "applications",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column(
            "company_snapshot",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "product_snapshot",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(precision=29, scale=2), nullable=True),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("annual_revenue", sa.Numeric(precision=29, scale=2), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "CHECKING",
                "SCORING",
                "NEEDS_INFO",
                "MANUAL_REVIEW",
                "APPROVED",
                "COUNTER_OFFER",
                "REJECTED",
                "OFFER_ACCEPTED",
                "SIGNED",
                "ISSUED",
                "CANCELLED",
                name="application_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("current_decision_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_offer_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_applications_amount_positive")),
        sa.CheckConstraint("annual_revenue >= 0", name=op.f("ck_applications_revenue_nonnegative")),
        sa.CheckConstraint("revision > 0", name=op.f("ck_applications_revision_positive")),
        sa.CheckConstraint("term_months > 0", name=op.f("ck_applications_term_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_applications_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_applications_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_applications_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
    )
    op.create_index(
        op.f("ix_applications_accepted_offer_id"),
        "applications",
        ["accepted_offer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_applications_company_id"), "applications", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_applications_current_decision_id"),
        "applications",
        ["current_decision_id"],
        unique=False,
    )
    op.create_index(
        "ix_applications_owner_updated_id",
        "applications",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_applications_product_id"), "applications", ["product_id"], unique=False
    )
    op.create_index(
        "ix_applications_status_updated_id",
        "applications",
        ["status", "updated_at", "id"],
        unique=False,
    )
    op.create_table(
        "application_events",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "status_changed",
                "application_updated",
                "decision_created",
                "document_uploaded",
                name="event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "previous_status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "CHECKING",
                "SCORING",
                "NEEDS_INFO",
                "MANUAL_REVIEW",
                "APPROVED",
                "COUNTER_OFFER",
                "REJECTED",
                "OFFER_ACCEPTED",
                "SIGNED",
                "ISSUED",
                "CANCELLED",
                name="previous_application_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "CHECKING",
                "SCORING",
                "NEEDS_INFO",
                "MANUAL_REVIEW",
                "APPROVED",
                "COUNTER_OFFER",
                "REJECTED",
                "OFFER_ACCEPTED",
                "SIGNED",
                "ISSUED",
                "CANCELLED",
                name="application_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "actor_kind",
            sa.Enum(
                "client",
                "underwriter",
                "system",
                name="actor_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("public_message", sa.Text(), nullable=False),
        sa.Column("audit_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("revision > 0", name=op.f("ck_application_events_revision_positive")),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_application_events_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_application_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_events_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_events")),
        sa.UniqueConstraint(
            "application_id", "sequence", name=op.f("uq_application_events_application_id")
        ),
    )
    op.create_index(
        op.f("ix_application_events_actor_user_id"),
        "application_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_table(
        "decisions",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "APPROVED",
                "COUNTER_OFFER",
                "NEEDS_INFO",
                "MANUAL_REVIEW",
                "REJECTED",
                name="outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "automatic", "underwriter", name="source", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=True),
        sa.Column("public_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("public_explanation", sa.Text(), nullable=False),
        sa.Column("required_documents", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scoring_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("analytical_explanation", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_revision > 0", name=op.f("ck_decisions_input_revision_positive")),
        sa.CheckConstraint("revision > 0", name=op.f("ck_decisions_revision_positive")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_decisions_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_decisions_author_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
        sa.UniqueConstraint("application_id", "id", name=op.f("uq_decisions_application_id")),
    )
    op.create_index(
        "ix_decisions_application_revision",
        "decisions",
        ["application_id", "revision"],
        unique=False,
    )
    op.create_index(op.f("ix_decisions_author_id"), "decisions", ["author_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("application_revision", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("application_revision > 0", name=op.f("ck_documents_revision_positive")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_documents_size_nonnegative")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_documents_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_documents_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        "ix_documents_application_created",
        "documents",
        ["application_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_documents_uploaded_by"), "documents", ["uploaded_by"], unique=False)
    op.create_table(
        "offers",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("amount", sa.Numeric(precision=29, scale=2), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_offers_amount_positive")),
        sa.CheckConstraint("annual_rate_percent >= 0", name=op.f("ck_offers_rate_nonnegative")),
        sa.CheckConstraint("term_months > 0", name=op.f("ck_offers_term_positive")),
        sa.ForeignKeyConstraint(
            ["application_id", "decision_id"],
            ["decisions.application_id", "decisions.id"],
            name="fk_offers_application_decision",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_offers_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_offers_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
        sa.UniqueConstraint("application_id", "id", name=op.f("uq_offers_application_id")),
        sa.UniqueConstraint("decision_id", name=op.f("uq_offers_decision_id")),
    )
    op.create_index(op.f("ix_offers_product_id"), "offers", ["product_id"], unique=False)

    # History tables now exist; add both reverse links explicitly to resolve the cycle.
    op.create_foreign_key(
        "fk_applications_current_decision",
        "applications",
        "decisions",
        ["id", "current_decision_id"],
        ["application_id", "id"],
    )
    op.create_foreign_key(
        "fk_applications_accepted_offer",
        "applications",
        "offers",
        ["id", "accepted_offer_id"],
        ["application_id", "id"],
    )


def downgrade() -> None:
    # Remove reverse links before dropping history; never use DROP ... CASCADE.
    op.drop_constraint("fk_applications_accepted_offer", "applications", type_="foreignkey")
    op.drop_constraint("fk_applications_current_decision", "applications", type_="foreignkey")
    op.drop_index(op.f("ix_offers_product_id"), table_name="offers")
    op.drop_table("offers")
    op.drop_index(op.f("ix_documents_uploaded_by"), table_name="documents")
    op.drop_index("ix_documents_application_created", table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_decisions_author_id"), table_name="decisions")
    op.drop_index("ix_decisions_application_revision", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_application_events_actor_user_id"), table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_applications_status_updated_id", table_name="applications")
    op.drop_index(op.f("ix_applications_product_id"), table_name="applications")
    op.drop_index("ix_applications_owner_updated_id", table_name="applications")
    op.drop_index(op.f("ix_applications_current_decision_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_company_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_accepted_offer_id"), table_name="applications")
    op.drop_table("applications")
    op.drop_table("users")
    op.drop_table("products")
    op.drop_table("companies")
