"""Bounded reads of committed events; UUIDs identify cursors, sequence orders them."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApplicationEvent
from app.schemas.applications import ApplicationEvent as PublicEvent
from app.schemas.applications import EventList


async def cursor_sequence(db: AsyncSession, application_id: UUID, cursor: UUID) -> int:
    sequence = await db.scalar(
        select(ApplicationEvent.sequence).where(
            ApplicationEvent.id == cursor, ApplicationEvent.application_id == application_id
        )
    )
    if sequence is None:
        # No retention/tombstone mechanism exists: unknown does not mean expired.
        raise HTTPException(404)
    return sequence


async def latest_sequence(db: AsyncSession, application_id: UUID) -> int:
    return await db.scalar(
        select(func.coalesce(func.max(ApplicationEvent.sequence), 0)).where(
            ApplicationEvent.application_id == application_id
        )
    )


async def read_events(
    db: AsyncSession, application_id: UUID, after: int, limit: int
) -> list[tuple[int, PublicEvent]]:
    # Explicit SELECT as well as explicit schema: never fetch audit or bank fields.
    rows = await db.execute(
        select(
            ApplicationEvent.sequence,
            ApplicationEvent.id,
            ApplicationEvent.application_id,
            ApplicationEvent.revision,
            ApplicationEvent.event_type,
            ApplicationEvent.status,
            ApplicationEvent.previous_status,
            ApplicationEvent.occurred_at,
            ApplicationEvent.public_message.label("message"),
        )
        .where(ApplicationEvent.application_id == application_id, ApplicationEvent.sequence > after)
        .order_by(ApplicationEvent.sequence)
        .limit(limit)
    )
    return [
        (
            row.sequence,
            PublicEvent.model_validate({k: v for k, v in row.items() if k != "sequence"}),
        )
        for row in rows.mappings()
    ]


async def history_page(
    db: AsyncSession, application_id: UUID, cursor: UUID | None, limit: int
) -> EventList:
    after = await cursor_sequence(db, application_id, cursor) if cursor else 0
    rows = await read_events(db, application_id, after, limit + 1)
    items = [event for _, event in rows[:limit]]
    return EventList(items=items, next_cursor=items[-1].id if len(rows) > limit else None)
