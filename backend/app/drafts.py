"""Draft persistence, revision locking and explicit public projections."""

from decimal import Decimal

from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import company_identity, public_product
from app.models import Application, Decision, Offer, Product
from app.schemas.applications import ClientApplication, ClientApplicationSummary, DraftFields
from app.transitions import append_event

ACTION_MAP = {
    "DRAFT": ["edit", "submit", "cancel", "upload_document"],
    "SUBMITTED": ["cancel"],
    "CHECKING": ["cancel"],
    "SCORING": ["cancel"],
    "NEEDS_INFO": ["provide_info", "upload_document", "cancel"],
    "MANUAL_REVIEW": ["cancel"],
    "APPROVED": ["accept_offer", "cancel"],
    "COUNTER_OFFER": ["accept_offer", "cancel"],
    "OFFER_ACCEPTED": ["sign", "cancel"],
}


def money(value: Decimal | None) -> str | None:
    return format(value, ".2f") if value is not None else None


def summary(application: Application) -> ClientApplicationSummary:
    return ClientApplicationSummary(
        id=application.id,
        company_name=(application.company_snapshot or {}).get("name"),
        product_id=application.product_id,
        amount=money(application.amount),
        term_months=application.term_months,
        status=application.status,
        revision=application.revision,
        allowed_actions=ACTION_MAP.get(application.status, []),
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def card(db: AsyncSession, application: Application) -> ClientApplication:
    decision = None
    if application.current_decision_id is not None:
        row = await db.scalar(
            select(Decision).where(
                Decision.id == application.current_decision_id,
                Decision.application_id == application.id,
            )
        )
        if row is not None:
            decision = {
                "id": row.id,
                "created_at": row.created_at,
                "outcome": row.outcome,
                "reasons": row.public_reasons,
                "explanation": row.public_explanation,
                "required_documents": row.required_documents,
            }
            if row.outcome in {"APPROVED", "COUNTER_OFFER"}:
                offer = await db.scalar(
                    select(Offer).where(
                        Offer.decision_id == row.id, Offer.application_id == application.id
                    )
                )
                decision["offer"] = {
                    "id": offer.id,
                    "created_at": offer.created_at,
                    "expires_at": offer.expires_at,
                    "terms": {
                        "product_id": offer.product_id,
                        "amount": money(offer.amount),
                        "term_months": offer.term_months,
                        "annual_rate_percent": (
                            format(offer.annual_rate_percent, "f")
                            if offer.annual_rate_percent is not None
                            else None
                        ),
                    },
                }
    return ClientApplication(
        **summary(application).model_dump(),
        owner_id=application.owner_id,
        form=DraftFields(
            company=application.company_snapshot,
            product_id=application.product_id,
            amount=money(application.amount),
            term_months=application.term_months,
            purpose=application.purpose,
            annual_revenue=money(application.annual_revenue),
        ),
        decision=decision,
        accepted_offer_id=application.accepted_offer_id,
    )


async def apply_fields(db: AsyncSession, application: Application, body: DraftFields) -> None:
    # Validate references before acquiring any company locks or mutating the application.
    if "product_id" in body.model_fields_set:
        product = await db.get(Product, body.product_id) if body.product_id is not None else None
        if body.product_id is not None and (product is None or not product.active):
            raise RequestValidationError(
                [{"loc": ("body", "product_id"), "type": "reference_not_found"}]
            )
        application.product_id = body.product_id
        application.product_snapshot = (
            {
                **public_product(product).model_dump(mode="json"),
                "version": product.version,
                "policy": product.policy_snapshot,
            }
            if product is not None
            else None
        )
    if body.term_months is not None and body.term_months > 2147483647:
        raise RequestValidationError([{"loc": ("body", "term_months"), "type": "int_parsing_size"}])
    if "company" in body.model_fields_set:
        application.company_id = (
            await company_identity(db, body.company) if body.company is not None else None
        )
        application.company_snapshot = (
            body.company.model_dump(mode="json") if body.company is not None else None
        )
    for field in ("amount", "term_months", "purpose", "annual_revenue"):
        if field in body.model_fields_set:
            value = getattr(body, field)
            if field in {"amount", "annual_revenue"} and value is not None:
                value = Decimal(value)
            setattr(application, field, value)


async def audit(db, application, principal, request_id, *, created=False):
    await append_event(
        db,
        application,
        actor_kind=principal.user.role,
        actor_user_id=principal.user.id,
        request_id=request_id,
        event_type="application_updated",
        previous_status=None if created else application.status,
        public_message="Черновик создан" if created else "Черновик сохранён",
        audit_payload={},
    )
