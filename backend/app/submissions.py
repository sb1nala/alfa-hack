"""Submit/cancel commands. No network, scoring, or helper-level commits."""

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import drafts
from app.dependencies import Principal
from app.models import Application, ApplicationSubmission, PipelineJob, Product, SubmitIdempotency
from app.schemas.applications import CancelRequest, ClientApplication, SubmitRequest
from app.transitions import lock_application, transition


def job_applicable(application: Application, submission: ApplicationSubmission, job: PipelineJob):
    """B07 must recheck under locks before saving, also checking its lease token."""
    return (
        job.application_id == application.id == submission.application_id
        and job.submission_id == submission.id
        and application.input_revision == submission.input_revision
        and application.status in {"SUBMITTED", "CHECKING", "SCORING"}
        and job.state in {"pending", "running"}
    )


async def validate_complete(db: AsyncSession, application: Application) -> None:
    missing = []
    if not application.company_id or not application.company_snapshot:
        missing.append("company")
    # A shared product lock prevents an archive/update racing this validation.
    product = await db.scalar(
        select(Product).where(Product.id == application.product_id).with_for_update(read=True)
    )
    if product is None or not product.active or not application.product_snapshot:
        missing.append("product_id")
    if application.purpose is None or not application.purpose.strip():
        missing.append("purpose")
    if application.amount is None or application.amount <= 0:
        missing.append("amount")
    if application.term_months is None or application.term_months <= 0:
        missing.append("term_months")
    if missing:
        raise RequestValidationError(
            [{"loc": ("body", field), "type": "required_for_submit"} for field in missing]
        )


async def submit(
    db: AsyncSession,
    application_id: UUID,
    body: SubmitRequest,
    key: UUID,
    principal: Principal,
    request_id: UUID,
) -> ClientApplication:
    # Same serialization point for replay, submit, PATCH and cancel. No pending
    # idempotency row is needed: a concurrent submit waits, then reads the result.
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    canonical = body.model_dump(mode="json")
    previous = await db.scalar(
        select(SubmitIdempotency).where(
            SubmitIdempotency.user_id == principal.user.id,
            SubmitIdempotency.application_id == application.id,
            SubmitIdempotency.operation == "submit",
            SubmitIdempotency.key == key,
        )
    )
    if previous is not None:
        if previous.request_body != canonical:
            raise HTTPException(409)
        # Keep successful records until a future explicit retention cleanup. Even
        # after cancel/restart this is the original response, never today's card.
        return ClientApplication.model_validate(previous.response_body)
    if application.status != "DRAFT" or application.revision != body.expected_revision:
        raise HTTPException(409)
    await validate_complete(db, application)
    application = await transition(
        db,
        application_id,
        "SUBMITTED",
        expected_revision=body.expected_revision,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message="Заявка отправлена",
        audit_payload={"consent_to_checks": True},
    )
    application.input_revision = application.revision
    result = await drafts.card(db, application)
    submission = ApplicationSubmission(
        application_id=application.id,
        input_revision=application.input_revision,
        form_snapshot=result.form.model_dump(mode="json"),
        product_snapshot=application.product_snapshot,
        consent_to_checks=True,
        submitted_at=application.updated_at,
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
    db.add(
        SubmitIdempotency(
            user_id=principal.user.id,
            application_id=application.id,
            operation="submit",
            key=key,
            request_body=canonical,
            response_status=202,
            response_body=result.model_dump(mode="json"),
            created_at=submission.submitted_at,
            expires_at=submission.submitted_at + timedelta(hours=24),
        )
    )
    await db.flush()
    return result


async def cancel(
    db: AsyncSession,
    application_id: UUID,
    body: CancelRequest,
    principal: Principal,
    request_id: UUID,
) -> ClientApplication:
    await lock_application(db, application_id, owner_id=principal.user.id)
    application = await transition(
        db,
        application_id,
        "CANCELLED",
        expected_revision=body.expected_revision,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        public_message="Заявка отозвана",
        audit_payload={"reason": body.reason},
    )
    # Lock order for B07 persistence: application, then job. Claim transactions
    # must release job locks before acquiring application locks or doing I/O.
    await db.execute(
        update(PipelineJob)
        .where(
            PipelineJob.application_id == application.id,
            PipelineJob.state.in_(["pending", "running"]),
        )
        .values(state="cancelled", lease_token=None, lease_expires_at=None)
    )
    return await drafts.card(db, application)
