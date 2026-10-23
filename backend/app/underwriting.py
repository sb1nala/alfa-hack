"""B12 bank side: queue, bank card with decision history, manual decisions."""

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import drafts
from app.catalog import public_product
from app.dependencies import Principal
from app.models import Application, Decision, Offer, Product
from app.schemas.common import Page, PageQuery
from app.schemas.underwriting import (
    BankDecision,
    UnderwriterApplication,
    UnderwriterQueueItem,
)
from app.transitions import lock_application, transition

OFFER_OUTCOMES = frozenset({"APPROVED", "COUNTER_OFFER"})


async def queue(db: AsyncSession, status: str, page: PageQuery) -> Page[UnderwriterQueueItem]:
    # Oldest first: the analyst takes the application that has waited longest.
    filtered = select(Application).where(Application.status == status).cte("filtered")
    paged = (
        select(filtered)
        .order_by(filtered.c.updated_at.asc(), filtered.c.id.asc())
        .offset(page.offset)
        .limit(page.limit)
        .cte("paged")
    )
    total = select(func.count().label("total")).select_from(filtered).cte("total")
    item = aliased(Application, paged)
    rows = (
        await db.execute(
            select(total.c.total, item)
            .select_from(total.outerjoin(paged, true()))
            .order_by(paged.c.updated_at.asc(), paged.c.id.asc())
        )
    ).all()
    return Page(
        items=[
            UnderwriterQueueItem(
                id=row.id,
                owner_id=row.owner_id,
                company_name=(row.company_snapshot or {}).get("name"),
                status=row.status,
                revision=row.revision,
                updated_at=row.updated_at,
            )
            for _, row in rows
            if row is not None
        ],
        total=rows[0][0],
        offset=page.offset,
        limit=page.limit,
    )


def saved_review_reason(row: Decision) -> str | None:
    """Read this decision's snapshot, independently of the application's current status."""
    if row.outcome != "MANUAL_REVIEW" or not isinstance(row.risk_result, dict):
        return None
    reason = row.risk_result.get("review_reason")
    return reason if isinstance(reason, str) and reason.strip() else None


async def bank_card(db: AsyncSession, application: Application) -> UnderwriterApplication:
    rows = await db.scalars(
        select(Decision)
        .where(Decision.application_id == application.id)
        .order_by(Decision.revision, Decision.created_at, Decision.id)
    )
    decisions = [
        BankDecision(
            public=await drafts.public_decision(db, row),
            source=row.source,
            author_id=row.author_id,
            policy_version=row.policy_version,
            scoring_factors=row.scoring_factors,
            analytical_explanation=row.analytical_explanation or "Без аналитического комментария",
            review_reason=saved_review_reason(row),
        )
        for row in list(rows)
    ]
    return UnderwriterApplication(
        application=await drafts.card(db, application),
        decisions=decisions,
        allowed_actions=["decide"] if application.status == "MANUAL_REVIEW" else [],
    )


async def offered_product(db: AsyncSession, product_id: UUID) -> Product:
    product = await db.scalar(
        select(Product).where(Product.id == product_id).with_for_update(read=True)
    )
    if product is None or not product.active:
        raise RequestValidationError(
            [{"loc": ("body", "offer_terms", "product_id"), "type": "reference_not_found"}]
        )
    return product


async def decide(
    db: AsyncSession,
    application_id: UUID,
    body,
    principal: Principal,
    request_id: UUID,
) -> UnderwriterApplication:
    application = await lock_application(db, application_id)
    if application.status != "MANUAL_REVIEW" or application.revision != body.expected_revision:
        raise HTTPException(409)
    product = None
    if body.outcome in OFFER_OUTCOMES:
        if body.offer_terms.term_months > 2147483647:
            raise RequestValidationError(
                [{"loc": ("body", "offer_terms", "term_months"), "type": "int_parsing_size"}]
            )
        product = await offered_product(db, body.offer_terms.product_id)
    row = Decision(
        application_id=application.id,
        input_revision=application.input_revision,
        revision=application.revision + 1,
        outcome=body.outcome,
        source="underwriter",
        author_id=principal.user.id,
        policy_version=None,
        public_reasons=[reason.model_dump(mode="json") for reason in body.reasons],
        public_explanation=body.explanation,
        required_documents=[
            document.model_dump(mode="json") for document in getattr(body, "required_documents", [])
        ],
        scoring_factors=[],
        analytical_explanation=body.analytical_explanation,
    )
    db.add(row)
    await db.flush()
    if product is not None:
        terms = body.offer_terms
        db.add(
            Offer(
                application_id=application.id,
                decision_id=row.id,
                product_id=product.id,
                product_snapshot={
                    **public_product(product).model_dump(mode="json"),
                    "version": product.version,
                    "policy": product.policy_snapshot,
                },
                amount=Decimal(terms.amount),
                term_months=terms.term_months,
                annual_rate_percent=(
                    Decimal(terms.annual_rate_percent)
                    if terms.annual_rate_percent is not None
                    else None
                ),
                expires_at=None,
            )
        )
    application.current_decision_id = row.id
    await db.flush()
    application = await transition(
        db,
        application_id,
        body.outcome,
        expected_revision=body.expected_revision,
        actor_kind="underwriter",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message=body.explanation,
        audit_payload={"decision_id": str(row.id), "source": "underwriter"},
    )
    return await bank_card(db, application)
