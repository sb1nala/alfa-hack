"""Operator-only repair command for a diagnosed failed pipeline job. No public API."""

import argparse
import asyncio
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import ApplicationSubmission, Decision, PipelineJob, SubmissionChecks
from app.transitions import append_event, lock_application, transition


async def requeue(db, job_id: UUID):
    application_id = await db.scalar(
        select(PipelineJob.application_id).where(PipelineJob.id == job_id)
    )
    if application_id is None:
        raise ValueError("Job not found")
    app = await lock_application(db, application_id)
    job = await db.scalar(
        select(PipelineJob)
        .where(PipelineJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    submission = await db.get(ApplicationSubmission, job.submission_id, populate_existing=True)
    checks = await db.get(SubmissionChecks, job.submission_id)
    decision = await db.scalar(
        select(Decision.id).where(Decision.submission_id == job.submission_id)
    )
    if (
        job.state != "failed"
        or job.kind != "application_pipeline"
        or submission is None
        or submission.application_id != app.id
        or app.input_revision != submission.input_revision
        or decision is not None
        or not (
            (job.phase == "checks" and app.status == "CHECKING" and checks is None)
            or (job.phase == "decision" and app.status == "SCORING" and checks is not None)
        )
    ):
        raise ValueError("Only a current failed pipeline job without a decision can be requeued")
    audit = {
        "job_id": str(job.id),
        "submission_id": str(job.submission_id),
        "phase": job.phase,
        "category": "operator_requeue",
        "previous_attempts": job.attempts,
        "previous_error_stage": job.last_error_stage,
        "previous_error_category": job.last_error_category,
    }
    if job.phase == "checks":
        await transition(
            db,
            app.id,
            "CHECKING",
            expected_revision=app.revision,
            actor_kind="system",
            actor_user_id=None,
            request_id=uuid4(),
            public_message="Проверка данных будет повторена",
            audit_payload=audit,
        )
    else:
        # SCORING -> SCORING is not a documented transition; audit a technical update.
        app.revision += 1
        app.updated_at = await db.scalar(select(func.clock_timestamp()))
        await append_event(
            db,
            app,
            actor_kind="system",
            actor_user_id=None,
            request_id=uuid4(),
            event_type="application_updated",
            previous_status=app.status,
            public_message="Расчёт решения будет повторён",
            audit_payload=audit,
        )
    job.state = "pending"
    job.attempts = 0
    # Both branches use database time while holding the application lock.
    job.available_at = app.updated_at
    job.lease_token = job.lease_expires_at = None
    job.last_error_stage = job.last_error_category = None
    await db.flush()


async def main(job_id: UUID):
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine).begin() as db:
            await requeue(db, job_id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args()
    asyncio.run(main(args.job_id))
