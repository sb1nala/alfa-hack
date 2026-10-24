"""Pipeline summary for the analyst cabinet: aggregates only, read-only, one snapshot."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import drafts
from app.models import Application, ApplicationSubmission, Decision, Offer
from app.schemas.common import ApplicationStatus
from app.schemas.underwriting import PipelineStats

IN_PROGRESS = (
    "SUBMITTED",
    "CHECKING",
    "SCORING",
    "NEEDS_INFO",
    "MANUAL_REVIEW",
    "APPROVED",
    "COUNTER_OFFER",
    "OFFER_ACCEPTED",
    "SIGNED",
)


async def pipeline(db: AsyncSession) -> PipelineStats:
    # Several short reads: a request landing in between may shift one figure by one,
    # which a dashboard tolerates; each figure is consistent on its own.
    now = await db.scalar(select(func.clock_timestamp()))
    counts = dict(
        (
            await db.execute(
                select(Application.status, func.count())
                .where(Application.status != "DRAFT")
                .group_by(Application.status)
            )
        ).all()
    )
    manual_count, oldest = (
        await db.execute(
            select(func.count(), func.min(Application.updated_at)).where(
                Application.status == "MANUAL_REVIEW"
            )
        )
    ).one()

    # The first decision of each application: the system's own verdict or routing.
    first = (
        select(
            Decision.application_id,
            Decision.source,
            Decision.outcome,
            Decision.created_at,
        )
        .distinct(Decision.application_id)
        .order_by(Decision.application_id, Decision.created_at, Decision.revision)
        .subquery()
    )
    submitted = (
        select(
            ApplicationSubmission.application_id,
            func.min(ApplicationSubmission.submitted_at).label("submitted_at"),
        )
        .group_by(ApplicationSubmission.application_id)
        .subquery()
    )
    seconds = func.extract("epoch", first.c.created_at - submitted.c.submitted_at)
    decided, automated, median, p90 = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(
                    first.c.source == "automatic", first.c.outcome != "MANUAL_REVIEW"
                ),
                func.percentile_cont(0.5).within_group(seconds),
                func.percentile_cont(0.9).within_group(seconds),
            ).select_from(
                first.join(submitted, submitted.c.application_id == first.c.application_id)
            )
        )
    ).one()

    issued_count, issued_amount = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Offer.amount), 0))
            .select_from(Application)
            .join(Offer, Offer.id == Application.accepted_offer_id)
            .where(Application.status == "ISSUED")
        )
    ).one()

    return PipelineStats(
        generated_at=now,
        by_status=[
            {"status": status.value, "count": counts.get(status.value, 0)}
            for status in ApplicationStatus
            if status.value != "DRAFT"
        ],
        in_progress=sum(counts.get(status, 0) for status in IN_PROGRESS),
        manual_queue={
            "count": manual_count,
            "oldest_waiting_seconds": (
                max(0, int((now - oldest).total_seconds())) if oldest is not None else None
            ),
        },
        decided=decided,
        automated=automated,
        decision_time={
            "median_seconds": round(float(median), 1) if median is not None else None,
            "p90_seconds": round(float(p90), 1) if p90 is not None else None,
        },
        issued={"count": issued_count, "amount": drafts.money(Decimal(issued_amount))},
    )
