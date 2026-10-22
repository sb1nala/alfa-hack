"""PostgreSQL is the sole source of truth; no listener, broker or per-client queue."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from anyio import CancelScope
from asyncpg import PostgresError
from fastapi import HTTPException, Request
from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from app.dependencies import current_principal, load_application
from app.event_history import cursor_sequence, latest_sequence, read_events
from app.schemas.applications import ApplicationEvent, SSEData

logger = logging.getLogger(__name__)
data_adapter = TypeAdapter(SSEData)
HEARTBEAT_INTERVAL = 15.0


@dataclass(frozen=True)
class StreamStart:
    application_id: UUID
    sequence: int


async def check_access(request: Request, db, application_id: UUID) -> None:
    # Reuse all JWT, session, user-role and ownership checks, with a fresh session.
    principal = await current_principal(request, db)
    if principal.user.role not in {"client", "underwriter"}:
        raise HTTPException(403)
    await load_application(application_id, db, principal, allow_underwriter=True)


@asynccontextmanager
async def event_database(request: Request):
    # Starlette disconnect uses level cancellation. Shield this bounded operation
    # so repeated cancellation cannot interrupt asyncpg rollback/termination.
    with CancelScope(shield=True):
        async with asyncio.timeout(request.app.state.settings.sse_db_timeout):
            async with request.app.state.session_factory() as db:
                yield db


async def prepare_stream(
    request: Request, application_id: UUID, last_event_id: UUID | None
) -> StreamStart:
    async with event_database(request) as db:
        await check_access(request, db, application_id)
        sequence = (
            await cursor_sequence(db, application_id, last_event_id)
            if last_event_id
            else await latest_sequence(db, application_id)
        )
    # Closed BEFORE StreamingResponse sends 200. No yield database dependency.
    # Writers hold the application lock through sequence allocation and commit;
    # a later committed event cannot hide below this committed high-water mark.
    return StreamStart(application_id, sequence)


def event_frame(event: ApplicationEvent) -> str:
    fields = {
        "event_type": event.event_type,
        "application_id": event.application_id,
        "revision": event.revision,
        "status": event.status,
        "occurred_at": event.occurred_at,
    }
    if event.event_type == "status_changed":
        fields["previous_status"] = event.previous_status
    data = data_adapter.validate_python(fields)
    return f"id: {event.id}\nevent: {event.event_type}\ndata: {data.model_dump_json()}\n\n"


async def stream_events(request: Request, start: StreamStart) -> AsyncIterator[str]:
    config = request.app.state.settings
    loop = asyncio.get_running_loop()
    after = start.sequence
    auth_at = loop.time()  # Recheck even if response startup/send was slow.
    heartbeat_at = loop.time() + HEARTBEAT_INTERVAL
    try:
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            async with event_database(request) as db:
                if loop.time() >= auth_at:
                    await check_access(request, db, start.application_id)
                    auth_at = loop.time() + config.sse_access_interval
                rows = await read_events(db, start.application_id, after, config.sse_batch_size)
            # Every DB context is closed before yielding or waiting for the client.
            for sequence, event in rows:
                if loop.time() >= heartbeat_at:
                    yield ": heartbeat\n\n"
                    heartbeat_at = loop.time() + HEARTBEAT_INTERVAL
                # A heartbeat send may itself have crossed the access deadline.
                if loop.time() >= auth_at:
                    async with event_database(request) as db:
                        await check_access(request, db, start.application_id)
                    auth_at = loop.time() + config.sse_access_interval
                yield event_frame(event)
                after = sequence
            if loop.time() >= heartbeat_at:
                yield ": heartbeat\n\n"
                heartbeat_at = loop.time() + HEARTBEAT_INTERVAL
            if len(rows) < config.sse_batch_size:
                await asyncio.sleep(
                    max(
                        0,
                        min(
                            config.sse_poll_interval,
                            heartbeat_at - loop.time(),
                            auth_at - loop.time(),
                        ),
                    )
                )
    except HTTPException:
        # Headers are already sent; end the stream, never try to emit an HTTP 401.
        return
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        # No raw driver exception/credentials/audit data in logs or SSE.
        logger.warning("SSE database unavailable; reconnect using the last received event id")
        return
    # CancelledError intentionally propagates; async contexts release resources.


class EventStreamResponse(StreamingResponse):
    """Bound transport backpressure too. The generator owns at most one DB batch."""

    def __init__(self, content, *, send_timeout: float):
        self.send_timeout = send_timeout
        super().__init__(
            content,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def stream_response(self, send):
        async def bounded_send(message):
            async with asyncio.timeout(self.send_timeout):
                await send(message)

        try:
            await super().stream_response(bounded_send)
        finally:
            await self.body_iterator.aclose()
