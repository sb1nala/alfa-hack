"""B03 persistence schema. Business transitions belong to later service stages."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, get_args
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.schemas.applications import ApplicationEvent as EventSchema
from app.schemas.catalog import CompanyInput
from app.schemas.common import ApplicationStatus, Role
from app.schemas.decisions import PublicDecision
from app.schemas.underwriting import BankDecision


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True), Decimal: Numeric(29, 2), str: Text}


def api_enum(name: str, *values: str) -> Enum:
    # VARCHAR + CHECK avoids a separate PostgreSQL ENUM lifecycle, including shared statuses.
    return Enum(*values, name=name, native_enum=False, create_constraint=True)


def status_type(name: str = "application_status") -> Enum:
    return api_enum(name, *(item.value for item in ApplicationStatus))


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Identity, CreatedAt, Base):
    __tablename__ = "users"

    login: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    display_name: Mapped[str]
    role: Mapped[str] = mapped_column(api_enum("role", *(item.value for item in Role)))


class Company(Identity, Base):
    __tablename__ = "companies"
    __table_args__ = (CheckConstraint("inn ~ '^([0-9]{10}|[0-9]{12})$'", name="inn_format"),)

    inn: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str]
    legal_form: Mapped[str] = mapped_column(
        api_enum("legal_form", *get_args(CompanyInput.model_fields["legal_form"].annotation))
    )
    registration_date: Mapped[date | None]
    address: Mapped[str | None]
    okved: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Product(Identity, Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("version > 0", name="version_positive"),)

    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    description: Mapped[str]
    purposes: Mapped[list[str]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(server_default=text("1"))
    active: Mapped[bool] = mapped_column(server_default=text("true"))


class Application(Identity, CreatedAt, Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("term_months > 0", name="term_positive"),
        CheckConstraint("annual_revenue >= 0", name="revenue_nonnegative"),
        CheckConstraint("revision > 0", name="revision_positive"),
        # Named, separately added FKs break the applications -> history -> applications cycle.
        ForeignKeyConstraint(
            ["id", "current_decision_id"],
            ["decisions.application_id", "decisions.id"],
            name="fk_applications_current_decision",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "accepted_offer_id"],
            ["offers.application_id", "offers.id"],
            name="fk_applications_accepted_offer",
            use_alter=True,
        ),
        Index("ix_applications_owner_updated_id", "owner_id", "updated_at", "id"),
        Index("ix_applications_status_updated_id", "status", "updated_at", "id"),
    )

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), index=True)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True)
    company_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    product_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    amount: Mapped[Decimal | None]
    term_months: Mapped[int | None]
    purpose: Mapped[str | None]
    annual_revenue: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(status_type(), server_default=text("'DRAFT'"))
    revision: Mapped[int] = mapped_column(server_default=text("1"))
    current_decision_id: Mapped[UUID | None] = mapped_column(index=True)
    accepted_offer_id: Mapped[UUID | None] = mapped_column(index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    owner: Mapped[User] = relationship()
    company: Mapped[Company | None] = relationship()
    product: Mapped[Product | None] = relationship()
    current_decision: Mapped["Decision | None"] = relationship(
        foreign_keys=[current_decision_id],
        post_update=True,
        primaryjoin="and_(Application.id == Decision.application_id, "
        "Application.current_decision_id == Decision.id)",
    )
    accepted_offer: Mapped["Offer | None"] = relationship(
        foreign_keys=[accepted_offer_id],
        post_update=True,
        primaryjoin="and_(Application.id == Offer.application_id, "
        "Application.accepted_offer_id == Offer.id)",
    )


class ApplicationEvent(Identity, Base):
    __tablename__ = "application_events"
    __table_args__ = (
        UniqueConstraint("application_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )

    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int]
    event_type: Mapped[str] = mapped_column(
        api_enum("event_type", *get_args(EventSchema.model_fields["event_type"].annotation))
    )
    previous_status: Mapped[str | None] = mapped_column(status_type("previous_application_status"))
    status: Mapped[str] = mapped_column(status_type())
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_kind: Mapped[str] = mapped_column(
        api_enum("actor_kind", *(item.value for item in Role), "system")
    )
    request_id: Mapped[UUID]
    public_message: Mapped[str]
    audit_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())

    application: Mapped[Application] = relationship()
    actor: Mapped[User | None] = relationship()


class Decision(Identity, CreatedAt, Base):
    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("application_id", "id"),
        CheckConstraint("input_revision > 0", name="input_revision_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
        Index("ix_decisions_application_revision", "application_id", "revision"),
    )

    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    input_revision: Mapped[int]
    revision: Mapped[int]
    outcome: Mapped[str] = mapped_column(
        api_enum(
            "outcome",
            *(
                get_args(model.model_fields["outcome"].annotation)[0]
                for model in get_args(get_args(PublicDecision)[0])
            ),
        )
    )
    source: Mapped[str] = mapped_column(
        api_enum("source", *get_args(BankDecision.model_fields["source"].annotation))
    )
    author_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    policy_version: Mapped[str | None]
    public_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    public_explanation: Mapped[str]
    required_documents: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    scoring_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    analytical_explanation: Mapped[str]

    application: Mapped[Application] = relationship(foreign_keys=[application_id])
    author: Mapped[User | None] = relationship()


class Offer(Identity, CreatedAt, Base):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("application_id", "id"),
        ForeignKeyConstraint(
            ["application_id", "decision_id"],
            ["decisions.application_id", "decisions.id"],
            name="fk_offers_application_decision",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("term_months > 0", name="term_positive"),
        CheckConstraint("annual_rate_percent >= 0", name="rate_nonnegative"),
    )

    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    decision_id: Mapped[UUID] = mapped_column(unique=True)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    amount: Mapped[Decimal]
    term_months: Mapped[int]
    annual_rate_percent: Mapped[Decimal | None] = mapped_column(Numeric())
    expires_at: Mapped[datetime | None]

    application: Mapped[Application] = relationship(foreign_keys=[application_id])
    decision: Mapped[Decision] = relationship(foreign_keys=[decision_id])
    product: Mapped[Product] = relationship()


class Document(Identity, CreatedAt, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("application_revision > 0", name="revision_positive"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        Index("ix_documents_application_created", "application_id", "created_at"),
    )

    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    application_revision: Mapped[int]
    document_type: Mapped[str]
    filename: Mapped[str]
    media_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str]
    sha256: Mapped[str]

    application: Mapped[Application] = relationship()
    uploader: Mapped[User] = relationship()


class AuthSession(Identity, CreatedAt, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="revocation_time"),
        Index("ix_auth_sessions_user_revoked", "user_id", "revoked_at"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(api_enum("session_role", *(item.value for item in Role)))
    expires_at: Mapped[datetime] = mapped_column(index=True)
    revoked_at: Mapped[datetime | None]


class CSRFContext(CreatedAt, Base):
    __tablename__ = "csrf_contexts"
    __table_args__ = (
        CheckConstraint("length(cookie_digest) = 64", name="digest_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    cookie_digest: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
