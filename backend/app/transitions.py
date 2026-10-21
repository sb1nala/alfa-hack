"""Internal state machine. Commands own business preconditions and transactions."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, ApplicationEvent

# docs/application-states.md: graph and initiators are deliberately separate.
CANCELLABLE = frozenset(
    {
        "DRAFT",
        "SUBMITTED",
        "CHECKING",
        "SCORING",
        "NEEDS_INFO",
        "MANUAL_REVIEW",
        "APPROVED",
        "COUNTER_OFFER",
        "OFFER_ACCEPTED",
    }
)
TRANSITIONS = {
    "DRAFT": {"SUBMITTED"},
    "SUBMITTED": {"CHECKING"},
    "CHECKING": {"CHECKING", "SCORING"},
    "SCORING": {"APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "MANUAL_REVIEW", "REJECTED"},
    "NEEDS_INFO": {"CHECKING"},
    "MANUAL_REVIEW": {"APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "REJECTED"},
    "APPROVED": {"OFFER_ACCEPTED"},
    "COUNTER_OFFER": {"OFFER_ACCEPTED"},
    "OFFER_ACCEPTED": {"SIGNED"},
    "SIGNED": {"ISSUED"},
}
TRANSITIONS = {
    source: targets | ({"CANCELLED"} if source in CANCELLABLE else set())
    for source, targets in TRANSITIONS.items()
}
CLIENT_TRANSITIONS = {
    ("DRAFT", "SUBMITTED"),
    ("NEEDS_INFO", "CHECKING"),
    ("APPROVED", "OFFER_ACCEPTED"),
    ("COUNTER_OFFER", "OFFER_ACCEPTED"),
    ("OFFER_ACCEPTED", "SIGNED"),
} | {(source, "CANCELLED") for source in CANCELLABLE}


def transition_allowed(source: str, target: str) -> bool:
    return target in TRANSITIONS.get(source, set())


def initiator_allowed(source: str, target: str, actor_kind: str) -> bool:
    if not transition_allowed(source, target):
        return False
    if actor_kind == "client":
        return (source, target) in CLIENT_TRANSITIONS
    if actor_kind == "underwriter":
        return source == "MANUAL_REVIEW" and target != "CANCELLED"
    if actor_kind == "system":
        return (source, target) not in CLIENT_TRANSITIONS and source != "MANUAL_REVIEW"
    return False


async def lock_application(
    db: AsyncSession, application_id: UUID, *, owner_id: UUID | None = None
) -> Application:
    query = select(Application).where(Application.id == application_id)
    if owner_id is not None:
        query = query.where(Application.owner_id == owner_id)
    application = await db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if application is None:
        raise HTTPException(404)
    return application


async def append_event(
    db: AsyncSession,
    application: Application,
    *,
    actor_kind: str,
    actor_user_id: UUID | None,
    request_id: UUID,
    event_type: str,
    previous_status: str | None,
    public_message: str,
    audit_payload: dict,
) -> None:
    """Caller holds the application lock (or just inserted it), until command commit.

    Flush preceding events before allocating another sequence, including when a
    command eventually needs multiple events for one revision. Never commits.
    """
    await db.flush()
    sequence = 1 + await db.scalar(
        select(func.coalesce(func.max(ApplicationEvent.sequence), 0)).where(
            ApplicationEvent.application_id == application.id
        )
    )
    db.add(
        ApplicationEvent(
            application_id=application.id,
            sequence=sequence,
            revision=application.revision,
            event_type=event_type,
            previous_status=previous_status,
            status=application.status,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            request_id=request_id,
            public_message=public_message,
            audit_payload=audit_payload,
            occurred_at=application.updated_at,
        )
    )


async def transition(
    db: AsyncSession,
    application_id: UUID,
    target: str,
    *,
    expected_revision: int,
    actor_kind: str,
    actor_user_id: UUID | None,
    request_id: UUID,
    public_message: str,
    audit_payload: dict,
) -> Application:
    """Internal primitive, never an API. Business commands must check their inputs.

    Acquires/reuses the row lock; caller must keep it through all related writes.
    Client callers must also authorize ownership before invoking this function.
    """
    application = await lock_application(db, application_id)
    source = application.status
    if not transition_allowed(source, target) or application.revision != expected_revision:
        raise HTTPException(409)
    if not initiator_allowed(source, target, actor_kind):
        raise HTTPException(403)
    application.status = target
    application.revision += 1
    application.updated_at = await db.scalar(select(func.clock_timestamp()))
    await append_event(
        db,
        application,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        request_id=request_id,
        event_type="status_changed",
        previous_status=source,
        public_message=public_message,
        audit_payload=audit_payload,
    )
    return application
