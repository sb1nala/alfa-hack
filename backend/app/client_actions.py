"""B11 client commands after a decision. No network, scoring, or helper-level commits."""

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import documents, drafts
from app.dependencies import Principal
from app.models import ApplicationSubmission, Decision, Document, Offer, PipelineJob
from app.schemas.applications import (
    AcceptOfferRequest,
    AdditionalInfoRequest,
    ClientApplication,
    SignRequest,
)
from app.transitions import lock_application, transition


async def current_offer(db: AsyncSession, application) -> Offer | None:
    if application.current_decision_id is None:
        return None
    return await db.scalar(
        select(Offer)
        .join(Decision, Decision.id == Offer.decision_id)
        .where(
            Offer.application_id == application.id,
            Decision.id == application.current_decision_id,
            Decision.outcome.in_(["APPROVED", "COUNTER_OFFER"]),
        )
    )


async def accept_offer(
    db: AsyncSession,
    application_id: UUID,
    body: AcceptOfferRequest,
    principal: Principal,
    request_id: UUID,
) -> ClientApplication:
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    if (
        application.status not in {"APPROVED", "COUNTER_OFFER"}
        or application.revision != body.expected_revision
    ):
        raise HTTPException(409)
    offer = await current_offer(db, application)
    now = await db.scalar(select(func.clock_timestamp()))
    # Only the offer of the latest decision; an older or foreign offer is a conflict.
    if offer is None or offer.id != body.offer_id or (offer.expires_at and offer.expires_at <= now):
        raise HTTPException(409)
    application = await transition(
        db,
        application_id,
        "OFFER_ACCEPTED",
        expected_revision=body.expected_revision,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message="Предложение принято",
        audit_payload={"offer_id": str(offer.id)},
    )
    application.accepted_offer_id = offer.id
    await db.flush()
    return await drafts.card(db, application)


async def sign(
    db: AsyncSession,
    application_id: UUID,
    body: SignRequest,
    principal: Principal,
    request_id: UUID,
) -> ClientApplication:
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    if (
        application.status != "OFFER_ACCEPTED"
        or application.revision != body.expected_revision
        or application.accepted_offer_id != body.offer_id
    ):
        raise HTTPException(409)
    application = await transition(
        db,
        application_id,
        "SIGNED",
        expected_revision=body.expected_revision,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message="Договор подписан",
        audit_payload={"offer_id": str(body.offer_id), "consent": True, "simulation": True},
    )
    # Issuance is simulated by the system in the same transaction: SIGNED is audited,
    # but no client-visible state can stop between signing and issuing.
    application = await transition(
        db,
        application_id,
        "ISSUED",
        expected_revision=application.revision,
        actor_kind="system",
        actor_user_id=None,
        request_id=request_id,
        public_message="Деньги выданы",
        audit_payload={"offer_id": str(body.offer_id), "simulation": True},
    )
    return await drafts.card(db, application)


async def additional_info(
    db: AsyncSession,
    application_id: UUID,
    body: AdditionalInfoRequest,
    principal: Principal,
    request_id: UUID,
) -> ClientApplication:
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    if application.status != "NEEDS_INFO" or application.revision != body.expected_revision:
        raise HTTPException(409)
    if body.document_ids:
        found = set(
            await db.scalars(
                select(Document.id).where(
                    Document.application_id == application.id,
                    Document.id.in_(body.document_ids),
                )
            )
        )
        if found != set(body.document_ids):
            raise RequestValidationError(
                [{"loc": ("body", "document_ids"), "type": "reference_not_found"}]
            )
    if "annual_revenue" in body.model_fields_set and body.annual_revenue is not None:
        application.annual_revenue = Decimal(body.annual_revenue)
        await db.flush()
    application = await transition(
        db,
        application_id,
        "CHECKING",
        expected_revision=body.expected_revision,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message="Данные отправлены на проверку",
        audit_payload={
            "comment": body.comment,
            "document_ids": [str(identity) for identity in body.document_ids],
            "annual_revenue_changed": body.annual_revenue is not None,
        },
    )
    # A new immutable input version; the previous decision stays visible until replaced.
    application.input_revision = application.revision
    result = await drafts.card(db, application)
    submission = ApplicationSubmission(
        application_id=application.id,
        input_revision=application.input_revision,
        form_snapshot=result.form.model_dump(mode="json"),
        product_snapshot=application.product_snapshot,
        consent_to_checks=True,
        submitted_at=application.updated_at,
        document_types=await documents.types(db, application),
    )
    db.add(submission)
    await db.flush()
    db.add(
        PipelineJob(
            application_id=application.id,
            submission_id=submission.id,
            kind="application_pipeline",
            available_at=submission.submitted_at,
        )
    )
    await db.flush()
    return result
