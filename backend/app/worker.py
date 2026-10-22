"""PostgreSQL checks/decision worker. Run separately: python -m app.worker."""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, update

from app import decision_adapter, drafts
from app.check_sources import SourceError, fetch_checks
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import Application, ApplicationSubmission, PipelineJob, SubmissionChecks
from app.submissions import job_applicable
from app.transitions import lock_application, transition

logger = logging.getLogger(__name__)


class LostLease(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    application_id: UUID
    submission_id: UUID
    token: UUID
    attempt: int
    phase: str


class Worker:
    def __init__(self, sessions, client: httpx.AsyncClient, settings: Settings):
        self.sessions = sessions
        self.client = client
        self.settings = settings

    def log(self, claim: Claim, stage: str, category: str):
        logger.info(
            "job=%s application=%s submission=%s attempt=%s phase=%s stage=%s category=%s",
            claim.job_id,
            claim.application_id,
            claim.submission_id,
            claim.attempt,
            claim.phase,
            stage,
            category,
        )

    @asynccontextmanager
    async def transaction(self):
        async with (
            asyncio.timeout(
                self.settings.worker_db_timeout + self.settings.db_readiness_timeout + 2
            ),
            self.sessions() as db,
            db.begin(),
        ):
            milliseconds = str(int(self.settings.worker_db_timeout * 1000))
            await db.execute(select(func.set_config("statement_timeout", milliseconds, True)))
            await db.execute(select(func.set_config("lock_timeout", milliseconds, True)))
            yield db

    async def claim(self) -> Claim | None:
        async with self.transaction() as db:
            job = await db.scalar(
                select(PipelineJob)
                .where(
                    PipelineJob.phase.in_(["checks", "decision"]),
                    PipelineJob.kind == "application_pipeline",
                    or_(
                        and_(
                            PipelineJob.state == "pending", PipelineJob.available_at <= func.now()
                        ),
                        and_(
                            PipelineJob.state == "running",
                            PipelineJob.lease_expires_at <= func.now(),
                        ),
                    ),
                )
                .order_by(PipelineJob.available_at, PipelineJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            now = await db.scalar(select(func.clock_timestamp()))
            job.state = "running"
            job.lease_token = uuid4()
            job.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
            job.attempts += 1
            claim = Claim(
                job.id,
                job.application_id,
                job.submission_id,
                job.lease_token,
                job.attempts,
                job.phase,
            )
        self.log(claim, "claim", "acquired")
        return claim

    async def locked(self, db, claim: Claim):
        # Same order as cancel; refresh each row after acquiring its lock.
        app = await lock_application(db, claim.application_id)
        job = await db.scalar(
            select(PipelineJob)
            .where(PipelineJob.id == claim.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = await db.scalar(select(func.clock_timestamp()))
        if (
            job is None
            or job.application_id != claim.application_id
            or job.submission_id != claim.submission_id
            or job.phase != claim.phase
            or job.kind != "application_pipeline"
            or job.state != "running"
            or job.lease_token != claim.token
            or job.lease_expires_at <= now
        ):
            raise LostLease
        submission = await db.get(
            ApplicationSubmission, claim.submission_id, populate_existing=True
        )
        if (
            submission is None
            or not job_applicable(app, submission, job)
            or app.status
            not in ({"SUBMITTED", "CHECKING"} if claim.phase == "checks" else {"SCORING"})
        ):
            await self.fence(
                db,
                claim,
                state="cancelled",
                last_error_stage="inputs",
                last_error_category="stale_inputs",
                lease_token=None,
                lease_expires_at=None,
            )
            return None
        return app, job, submission

    async def fence(self, db, claim: Claim, *, input_revision=None, status=None, **values):
        # Recheck expiry after result/audit writes, immediately before commit.
        # Failure rolls back the entire transaction, including those inserts.
        await db.flush()
        conditions = [
            PipelineJob.id == claim.job_id,
            PipelineJob.application_id == claim.application_id,
            PipelineJob.submission_id == claim.submission_id,
            PipelineJob.kind == "application_pipeline",
            PipelineJob.phase == claim.phase,
            PipelineJob.state == "running",
            PipelineJob.lease_token == claim.token,
            PipelineJob.lease_expires_at > func.clock_timestamp(),
        ]
        if input_revision is not None:
            conditions.append(
                select(Application.id)
                .join(ApplicationSubmission, ApplicationSubmission.application_id == Application.id)
                .where(
                    Application.id == claim.application_id,
                    Application.status == status,
                    Application.input_revision == input_revision,
                    ApplicationSubmission.id == claim.submission_id,
                    ApplicationSubmission.input_revision == input_revision,
                )
                .exists()
            )
        result = await db.execute(
            update(PipelineJob)
            .where(*conditions)
            .values(**values)
            .returning(PipelineJob.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            raise LostLease

    async def move(self, db, app, claim, target, message, **audit):
        await transition(
            db,
            app.id,
            target,
            expected_revision=app.revision,
            actor_kind="system",
            actor_user_id=None,
            request_id=claim.token,
            public_message=message,
            audit_payload={
                "job_id": str(claim.job_id),
                "submission_id": str(claim.submission_id),
                "attempt": claim.attempt,
                "phase": claim.phase,
                **audit,
            },
        )

    async def start(self, claim: Claim) -> str | None:
        if claim.phase != "checks":
            raise LostLease
        async with self.transaction() as db:
            rows = await self.locked(db, claim)
            if rows is None:
                return None
            app, _, submission = rows
            if app.status == "SUBMITTED":
                await self.move(db, app, claim, "CHECKING", "Проверяем данные заявки")
            company = submission.form_snapshot.get("company")
            inn = company.get("inn") if isinstance(company, dict) else None
            await self.fence(db, claim, lease_token=claim.token)
        if (
            not isinstance(inn, str)
            or not inn.isascii()
            or not inn.isdigit()
            or len(inn) not in {10, 12}
        ):
            raise SourceError("inputs", "invalid_snapshot", retryable=False)
        return inn

    async def succeed(self, claim: Claim, checks: dict):
        if claim.phase != "checks":
            raise LostLease
        async with self.transaction() as db:
            rows = await self.locked(db, claim)
            if rows is None:
                return
            app, _, _ = rows
            if app.status != "CHECKING":
                raise LostLease
            db.add(SubmissionChecks(submission_id=claim.submission_id, **checks))
            await self.move(db, app, claim, "SCORING", "Данные получены, оцениваем заявку")
            await self.fence(
                db,
                claim,
                phase="decision",
                state="pending",
                attempts=0,
                available_at=func.clock_timestamp(),
                lease_token=None,
                lease_expires_at=None,
                last_error_stage=None,
                last_error_category=None,
            )
        self.log(claim, "checks", "handed_to_decision")

    async def fail(self, claim: Claim, error: SourceError):
        async with self.transaction() as db:
            rows = await self.locked(db, claim)
            if rows is None:
                return
            app, _, _ = rows
            retry = error.retryable and claim.attempt < self.settings.worker_max_attempts
            if retry:
                delay = min(
                    self.settings.worker_retry_max,
                    self.settings.worker_retry_base * 2 ** (claim.attempt - 1),
                )
                if claim.phase == "checks":
                    await self.move(
                        db,
                        app,
                        claim,
                        "CHECKING",
                        "Проверка данных будет повторена",
                        stage=error.stage,
                        category=error.category,
                        retry_seconds=delay,
                    )
            else:
                delay = 0
            await self.fence(
                db,
                claim,
                state="pending" if retry else "failed",
                available_at=func.clock_timestamp() + timedelta(seconds=delay),
                lease_token=None,
                lease_expires_at=None,
                last_error_stage=error.stage,
                last_error_category=error.category,
            )
        self.log(claim, error.stage, error.category)

    async def load_decision(self, claim: Claim):
        if claim.phase != "decision":
            raise LostLease
        async with self.transaction() as db:
            rows = await self.locked(db, claim)
            if rows is None:
                return None
            _, _, submission = rows
            checks = await db.get(SubmissionChecks, claim.submission_id)
            await self.fence(db, claim, lease_token=claim.token)
        return submission, checks

    async def save_decision(self, claim: Claim, result):
        if claim.phase != "decision":
            raise LostLease
        async with self.transaction() as db:
            rows = await self.locked(db, claim)
            if rows is None:
                return
            app, _, submission = rows
            row = await decision_adapter.save_result(db, app, submission, result)
            await self.move(
                db, app, claim, result.outcome, result.explanation, decision_id=str(row.id)
            )
            # Validate the existing public projection before committing anything.
            try:
                await drafts.card(db, app)
            except ValidationError:
                raise decision_adapter.incompatible("invalid_result") from None
            await self.fence(
                db,
                claim,
                state="completed",
                input_revision=submission.input_revision,
                status=result.outcome,
                lease_token=None,
                lease_expires_at=None,
                last_error_stage=None,
                last_error_category=None,
            )
        self.log(claim, "persistence", "completed")

    async def process(self, claim: Claim):
        stage = "start"
        try:
            async with asyncio.timeout(self.settings.worker_attempt_timeout):
                if claim.phase == "decision":
                    inputs = await self.load_decision(claim)
                    if inputs is None:
                        return
                else:
                    inn = await self.start(claim)
                    if inn is None:
                        return
                if claim.attempt > self.settings.worker_max_attempts:
                    raise SourceError(claim.phase, "attempts_exhausted", retryable=False)
                stage = claim.phase
                if claim.phase == "decision":
                    # No DB transaction or HTTP during the pure calculation.
                    result = await asyncio.to_thread(decision_adapter.calculate, *inputs)
                    stage = "persistence"
                    await self.save_decision(claim, result)
                else:
                    checks = await fetch_checks(self.client, inn, self.settings.worker_http_timeout)
                    stage = "persistence"
                    await self.succeed(claim, checks)
        except LostLease:
            self.log(claim, claim.phase, "lost_lease")
        except (SourceError, TimeoutError) as error:
            if isinstance(error, TimeoutError):
                if stage not in {"checks", "decision"}:
                    self.log(claim, stage, "database_timeout")
                    raise
                error = SourceError(stage, "attempt_timeout", retryable=True)
            try:
                await self.fail(claim, error)
            except LostLease:
                self.log(claim, claim.phase, "lost_lease")
        # DB errors propagate; the supervisor recovers by lease, never reports success.

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            claim = None
            try:
                claim = await self.claim()
                if claim is not None:
                    await self.process(claim)
                    continue
            except Exception:
                # Drivers can include secrets/parameters in exception text/tracebacks.
                if claim is not None:
                    self.log(claim, "worker", "internal_or_database")
                else:
                    logger.error("stage=poll category=database_or_internal")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.worker_poll_interval)
            except TimeoutError:
                pass


async def serve():
    settings = Settings()
    engine = create_engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with httpx.AsyncClient(base_url=settings.mocks_base_url, trust_env=False) as client:
            await Worker(create_session_factory(engine), client, settings).run(stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(serve())
