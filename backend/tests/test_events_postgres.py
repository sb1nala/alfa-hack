"""B09 uses PostgreSQL 16 and real TCP/Uvicorn streaming, with bounded waits."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from sqlalchemy import event as sql_event
from sqlalchemy import select, text, update
from test_auth_postgres import (
    ACCOUNTS,
    BANK,
    CLIENT,
    SECOND,
    SECRET,
    in_database,
)

from app import event_stream
from app.database import create_engine, create_session_factory
from app.main import create_app
from app.models import Application, ApplicationEvent, AuthSession, User
from app.security import decode_session, encode_session
from app.seed import import_accounts
from app.transitions import append_event, lock_application

pytestmark = pytest.mark.anyio
PUBLIC_FIELDS = {
    "id",
    "application_id",
    "revision",
    "event_type",
    "status",
    "previous_status",
    "occurred_at",
    "message",
}


@pytest.fixture(scope="session")
def event_settings(database):
    settings = database.model_copy(update={"auth_jwt_secret": SecretStr(SECRET)})

    async def seed(db):
        return await import_accounts(create_session_factory(db.bind), ACCOUNTS)

    # Other modules may have imported the same accounts already.
    assert sum(in_database(settings, seed)) == 10
    return settings


@pytest.fixture
async def live(event_settings):
    settings = event_settings.model_copy(
        update={
            "auth_cookie_secure": False,
            "sse_poll_interval": 0.05,
            "sse_access_interval": 0.1,
            "sse_batch_size": 2,
            "sse_db_timeout": 1.0,
            "sse_send_timeout": 0.3,
        }
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        settings.auth_allowed_origin = origin
        app = create_app(settings)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="critical",
                timeout_graceful_shutdown=0.3,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - observe Uvicorn readiness
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=2) as client:
            try:
                yield app, client, settings, server, task
            finally:
                server.should_exit = True
                async with asyncio.timeout(5):
                    await task


async def login(client, account=CLIENT):
    token = (await client.get("/api/auth/csrf")).json()["csrf_token"]
    response = await client.post(
        "/api/auth/login",
        json={
            "login": account["login"],
            "password": account["password"],
        },
        headers={"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": token},
    )
    assert response.status_code == 200
    return response.json()


async def change(app, callback):
    # A separate engine/connection represents an independent writer process.
    engine = create_engine(app.state.settings)
    try:
        async with create_session_factory(engine)() as db:
            result = await callback(db)
            await db.commit()
            return result
    finally:
        await engine.dispose()


async def new_application(app, login_name=CLIENT["login"]):
    async def write(db):
        owner = await db.scalar(select(User.id).where(User.login == login_name))
        application = Application(owner_id=owner)
        db.add(application)
        await db.flush()
        return application.id

    return await change(app, write)


async def add_events(db, application_id, types=("application_updated",), ids=None):
    application = await lock_application(db, application_id)
    for index, kind in enumerate(types):
        await append_event(
            db,
            application,
            actor_kind="underwriter",
            actor_user_id=application.owner_id,
            request_id=uuid4(),
            event_type=kind,
            previous_status="DRAFT" if kind == "status_changed" else None,
            public_message="Публичное сообщение №1 — заявка обновлена",
            audit_payload={"private_marker": "SECRET_RISK", "risk_result": {"score": 999}},
        )
        await db.flush()
        if ids:
            row = await db.scalar(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == application_id)
                .order_by(ApplicationEvent.sequence.desc())
                .limit(1)
            )
            row.id = ids[index]
    await db.flush()
    return list(
        (
            await db.scalars(
                select(ApplicationEvent.id)
                .where(ApplicationEvent.application_id == application_id)
                .order_by(ApplicationEvent.sequence)
            )
        ).all()
    )


async def write_events(app, application_id, types=("application_updated",), ids=None):
    return await change(app, lambda db: add_events(db, application_id, types, ids))


@asynccontextmanager
async def stream(client, application_id, cursor=None):
    headers = {"Last-Event-ID": str(cursor)} if cursor else {}
    async with client.stream(
        "GET", f"/api/applications/{application_id}/events", headers=headers
    ) as response:
        assert response.status_code == 200, await response.aread()
        yield response, response.aiter_lines()


async def frame(lines):
    result = []
    async with asyncio.timeout(2):
        async for line in lines:
            if not line:
                return "\n".join(result) + "\n\n"
            result.append(line)
    raise AssertionError("stream ended before frame")


async def business_frame(lines):
    while True:
        raw = await frame(lines)
        if raw.startswith("id: "):
            fields = dict(line.split(": ", 1) for line in raw.strip().splitlines())
            return fields, json.loads(fields["data"])


async def wait_empty_pool(app):
    async with asyncio.timeout(2):
        while app.state.engine.pool.checkedout():  # noqa: ASYNC110 - observe pool cleanup
            await asyncio.sleep(0.01)


async def assert_no_transaction(app):
    await wait_empty_pool(app)
    # Inspect from a different connection in AUTOCOMMIT so observation is not counted.
    engine = create_engine(app.state.settings)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL"
                    )
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_history_sequence_pagination_public_projection(live):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    path = f"/api/applications/{application_id}/history"
    assert (await client.get(path)).json() == {"items": [], "next_cursor": None}
    ids = [UUID(int=n) for n in [99, 2, 80, 1]]
    kinds = ("application_updated", "status_changed", "decision_created", "document_uploaded")
    await write_events(app, application_id, kinds, ids)
    pages = []
    cursor = None
    for _ in range(2):
        response = await client.get(
            path, params={"limit": 2, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200
        assert "SECRET_RISK" not in response.text
        page = response.json()
        pages.extend(page["items"])
        cursor = page["next_cursor"]
    assert cursor is None  # Exactly a full last page still has no continuation.
    assert [row["id"] for row in pages] == list(map(str, ids))
    assert [row["event_type"] for row in pages] == list(kinds)
    assert {row["revision"] for row in pages} == {1}
    assert all(set(row) == PUBLIC_FIELDS for row in pages)
    assert all(row["message"] == "Публичное сообщение №1 — заявка обновлена" for row in pages)
    assert (await client.get(path, params={"cursor": str(ids[-1])})).json() == {
        "items": [],
        "next_cursor": None,
    }
    # New continuation may appear after next_cursor=null: there is no TTL/snapshot token.
    await write_events(app, application_id)
    assert len((await client.get(path, params={"cursor": str(ids[-1])})).json()["items"]) == 1


async def test_history_defaults_limit_and_bounded_query(live):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id, ("application_updated",) * 103)
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "application_events" in statement:
            queries.append((statement, parameters))

    sql_event.listen(app.state.engine.sync_engine, "before_cursor_execute", capture)
    path = f"/api/applications/{application_id}/history"
    response = (await client.get(path)).json()
    assert len(response["items"]) == 50 and response["next_cursor"] == str(ids[49])
    assert len((await client.get(path, params={"limit": 100})).json()["items"]) == 100
    assert all("LIMIT" in sql for sql, _ in queries)
    assert queries[0][1][-1] == 51 and queries[1][1][-1] == 101
    assert all("audit_payload" not in sql and "actor_" not in sql for sql, _ in queries)
    for limit in (0, 101):
        assert (await client.get(path, params={"limit": limit})).status_code == 422


@pytest.mark.parametrize("endpoint", ["history", "events"])
async def test_access_and_cursors_before_open(live, endpoint):
    app, client, settings, *_ = live
    application_id = await new_application(app)
    ids = await write_events(app, application_id)
    other = await new_application(app, SECOND["login"])
    foreign = (await write_events(app, other))[0]
    path = f"/api/applications/{application_id}/{endpoint}"
    assert (await client.get(path)).status_code == 401
    await login(client, SECOND)
    assert (await client.get(path)).status_code == 404
    for account in (CLIENT, BANK):
        client.cookies.clear()
        await login(client, account)
        for cursor, expected in [(uuid4(), 404), (foreign, 404), ("invalid", 422)]:
            kwargs = (
                {"params": {"cursor": str(cursor)}}
                if endpoint == "history"
                else {"headers": {"Last-Event-ID": str(cursor)}}
            )
            response = await client.get(path, **kwargs)
            assert response.status_code == expected
            assert response.headers["content-type"] == "application/json"
            assert response.json()["request_id"] == response.headers["x-request-id"]
        if endpoint == "history":
            assert (await client.get(path)).status_code == 200
        else:
            async with stream(client, application_id, ids[-1]) as (_, lines):
                assert await frame(lines) == "retry: 3000\n\n"
    claims = decode_session(settings, client.cookies.get("access_token"))

    async def revoke(db):
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == UUID(claims["sid"]))
            .values(revoked_at=datetime.now(UTC))
        )

    await change(app, revoke)
    assert (await client.get(path)).status_code == 401


async def test_live_no_history_reconnect_exact_frames_and_headers(live, monkeypatch):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    old = await write_events(app, application_id)
    monkeypatch.setattr(event_stream, "HEARTBEAT_INTERVAL", 0.15)
    async with stream(client, application_id) as (response, lines):
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert await frame(lines) == "retry: 3000\n\n"
        assert await frame(lines) == ": heartbeat\n\n"  # No old event, no id.
        await assert_no_transaction(app)
        kinds = ("status_changed", "application_updated", "decision_created", "document_uploaded")
        ids = await write_events(app, application_id, kinds)
        for index, kind in enumerate(kinds, start=1):
            fields, data = await business_frame(lines)
            assert fields["id"] == str(ids[index])
            assert fields["event"] == data["event_type"] == kind
            expected = {"event_type", "application_id", "revision", "status", "occurred_at"}
            if kind == "status_changed":
                expected.add("previous_status")
                assert data["previous_status"] == "DRAFT"
            assert set(data) == expected
            assert "SECRET_RISK" not in fields["data"] and "message" not in data
            assert data["application_id"] == str(application_id)
        assert old[0] not in ids[1:]
    async with stream(client, application_id, ids[1]) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        for event_id in ids[2:]:
            fields, _ = await business_frame(lines)
            assert fields["id"] == str(event_id)
        assert await frame(lines) == ": heartbeat\n\n"
    await assert_no_transaction(app)


@pytest.mark.parametrize("reconnect", [False, True])
async def test_commit_between_boundary_and_first_read_and_after_replay(
    live, monkeypatch, reconnect
):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id)
    # Barrier after the actual pre-200 snapshot, before first polling query.
    from app.api import events as api

    original = api.prepare_stream
    ready, release = asyncio.Event(), asyncio.Event()

    async def prepare(*args):
        start = await original(*args)
        ready.set()
        await release.wait()
        return start

    monkeypatch.setattr(api, "prepare_stream", prepare)

    async def writer():
        await ready.wait()
        await write_events(app, application_id, ("status_changed",))
        release.set()

    task = asyncio.create_task(writer())
    async with stream(client, application_id, ids[0] if reconnect else None) as (_, lines):
        await task
        first, _ = await business_frame(lines)
        current = await write_events(app, application_id)
        assert first["id"] == str(current[1])
        # A commit after replay, during the same persistent sequence loop.
        second, _ = await business_frame(lines)
        assert second["id"] == str(current[2])


async def test_uncommitted_and_rollback_invisible_then_external_commit(live, monkeypatch):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    monkeypatch.setattr(event_stream, "HEARTBEAT_INTERVAL", 0.1)
    engine = create_engine(app.state.settings)
    try:
        async with create_session_factory(engine)() as writer:
            await add_events(writer, application_id)
            async with stream(client, application_id) as (_, lines):
                assert await frame(lines) == "retry: 3000\n\n"
                assert await frame(lines) == ": heartbeat\n\n"
                await writer.rollback()
                assert await frame(lines) == ": heartbeat\n\n"
                ids = await add_events(writer, application_id)
                assert await frame(lines) == ": heartbeat\n\n"
                await writer.commit()
                fields, _ = await business_frame(lines)
                assert fields["id"] == str(ids[0])
    finally:
        await engine.dispose()


@pytest.mark.parametrize("reason", ["logout", "expiry", "ownership"])
async def test_access_loss_ends_open_stream(live, reason):
    app, client, settings, *_ = live
    await login(client)
    application_id = await new_application(app)
    if reason == "expiry":
        claims = decode_session(settings, client.cookies.get("access_token"))

        async def shorten(db):
            session = await db.get(AuthSession, UUID(claims["sid"]))
            session.expires_at = datetime.now(UTC) + timedelta(seconds=1.5)
            user = await db.get(User, session.user_id)
            return encode_session(settings, user, session)

        token = await change(app, shorten)
        client.cookies.clear()
        client.cookies.set("access_token", token)
    async with stream(client, application_id) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        if reason == "logout":
            csrf = (await client.get("/api/auth/csrf")).json()["csrf_token"]
            assert (
                await client.post(
                    "/api/auth/logout",
                    headers={
                        "Origin": settings.auth_allowed_origin,
                        "X-CSRF-Token": csrf,
                    },
                )
            ).status_code == 204
        elif reason == "ownership":

            async def transfer(db):
                owner = await db.scalar(select(User.id).where(User.login == SECOND["login"]))
                await db.execute(
                    update(Application)
                    .where(Application.id == application_id)
                    .values(owner_id=owner)
                )

            await change(app, transfer)
        async with asyncio.timeout(2):
            assert [line async for line in lines] == []
    await assert_no_transaction(app)


async def test_db_failure_ends_stream_and_replay_recovers(live, monkeypatch):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id)
    original = event_stream.read_events
    entered = asyncio.Event()

    async def broken(db, *args):
        entered.set()
        # Real PostgreSQL failure aborts this read transaction.
        await db.execute(text("SELECT 1/0"))

    async with stream(client, application_id, ids[0]) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        monkeypatch.setattr(event_stream, "read_events", broken)
        async with asyncio.timeout(2):
            await entered.wait()
            assert [line async for line in lines] == []
    await assert_no_transaction(app)
    ids = await write_events(app, application_id, ("status_changed",))
    monkeypatch.setattr(event_stream, "read_events", original)
    async with stream(client, application_id, ids[0]) as (_, lines):
        fields, _ = await business_frame(lines)
        assert fields["id"] == str(ids[1])


async def test_db_stall_timeout_releases_connection(live, monkeypatch):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    entered = asyncio.Event()

    async def stalled(db, *args):
        entered.set()
        await db.execute(text("SELECT pg_sleep(10)"))

    monkeypatch.setattr(event_stream, "read_events", stalled)
    async with stream(client, application_id) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        async with asyncio.timeout(3):
            await entered.wait()
            assert [line async for line in lines] == []
    await assert_no_transaction(app)


async def test_slow_transport_bounded_batches_heartbeat_and_disconnect(live, monkeypatch):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id, ("application_updated",) * 12)
    monkeypatch.setattr(event_stream, "HEARTBEAT_INTERVAL", 0.05)
    original_read = event_stream.read_events
    reads, sent = [], []

    async def read(db, app_id, after, limit):
        rows = await original_read(db, app_id, after, limit)
        reads.append((after, limit, len(rows)))
        return rows

    monkeypatch.setattr(event_stream, "read_events", read)
    original_stream = event_stream.EventStreamResponse.stream_response

    async def slow_stream(self, send):
        async def slow_send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                await assert_no_transaction(app)
                sent.append(message["body"])
                await asyncio.sleep(0.06)  # Controlled backpressure over real TCP HTTP.
            await send(message)

        await original_stream(self, slow_send)

    monkeypatch.setattr(event_stream.EventStreamResponse, "stream_response", slow_stream)
    async with stream(client, application_id, ids[0]) as (_, lines):
        for event_id in ids[1:5]:
            fields, _ = await business_frame(lines)
            assert fields["id"] == str(event_id)
    await wait_empty_pool(app)
    assert reads and all(limit == 2 and size <= 2 for _, limit, size in reads)
    assert b": heartbeat\n\n" in sent  # Heartbeat also during continuous backlog.
    assert len(reads) < 6  # No unbounded prefetch while sending slowly.
    await assert_no_transaction(app)


async def test_cancel_during_query_and_shutdown_release_resources(live, monkeypatch):
    app, client, _, server, task = live
    await login(client)
    application_id = await new_application(app)
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def stalled(db, *args):
        entered.set()
        try:
            await db.execute(text("SELECT pg_sleep(10)"))
        finally:
            cleaned.set()

    monkeypatch.setattr(event_stream, "read_events", stalled)
    async with stream(client, application_id) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        await entered.wait()
    async with asyncio.timeout(3):
        await cleaned.wait()
    await assert_no_transaction(app)
    # Graceful Uvicorn shutdown is bounded even with an open SSE response.
    monkeypatch.undo()
    async with stream(client, application_id) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        server.should_exit = True
        async with asyncio.timeout(3):
            await task
    assert app.state.engine.pool.checkedout() == 0


async def test_send_deadline_closes_generator(live):
    app, client, *_ = live
    await login(client)
    # Exercise the actual response send deadline; TCP buffers cannot deterministically
    # force ASGI backpressure for a tiny event on every platform.
    closed = asyncio.Event()

    async def body():
        try:
            yield "retry: 3000\n\n"
            yield "data: unused\n\n"
        finally:
            closed.set()

    async def blocked_send(message):
        if message["type"] == "http.response.body":
            await assert_no_transaction(app)
            await asyncio.Event().wait()

    response = event_stream.EventStreamResponse(body(), send_timeout=0.05)
    with pytest.raises(TimeoutError):
        await response.stream_response(blocked_send)
    assert closed.is_set()


@pytest.mark.parametrize("reconnect", [False, True])
async def test_commit_after_empty_snapshot_is_not_lost(live, monkeypatch, reconnect):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id)
    original = event_stream.read_events
    empty_read, release = asyncio.Event(), asyncio.Event()
    intercepted = False

    async def read(db, *args):
        nonlocal intercepted
        rows = await original(db, *args)
        if not rows and not intercepted:
            intercepted = True
            empty_read.set()
            await release.wait()
        return rows

    monkeypatch.setattr(event_stream, "read_events", read)
    async with stream(client, application_id, ids[0] if reconnect else None) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        async with asyncio.timeout(2):
            await empty_read.wait()
        ids = await write_events(app, application_id)
        release.set()
        fields, _ = await business_frame(lines)
        assert fields["id"] == str(ids[-1])


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_preopen_db_failure_has_json_error(live, monkeypatch, failure):
    app, client, *_ = live
    await login(client)
    application_id = await new_application(app)

    async def broken(db, *args):
        await db.execute(text("SELECT 1/0" if failure == "error" else "SELECT pg_sleep(10)"))

    monkeypatch.setattr(event_stream, "latest_sequence", broken)
    response = await client.get(f"/api/applications/{application_id}/events")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "pg_sleep" not in response.text and "division" not in response.text
    await assert_no_transaction(app)


async def test_recheck_access_after_slow_heartbeat_before_event(live, monkeypatch):
    app, client, settings, *_ = live
    await login(client)
    application_id = await new_application(app)
    ids = await write_events(app, application_id, ("application_updated",) * 3)
    monkeypatch.setattr(event_stream, "HEARTBEAT_INTERVAL", 0.01)
    claims = decode_session(settings, client.cookies.get("access_token"))
    original = event_stream.EventStreamResponse.stream_response
    revoked = asyncio.Event()

    async def slow(self, send):
        async def delayed(message):
            if message.get("body") == b": heartbeat\n\n" and not revoked.is_set():

                async def revoke(db):
                    await db.execute(
                        update(AuthSession)
                        .where(AuthSession.id == UUID(claims["sid"]))
                        .values(revoked_at=datetime.now(UTC))
                    )

                await change(app, revoke)
                await asyncio.sleep(0.11)  # Cross the access check deadline during send.
                revoked.set()
            elif message.get("body") == b"retry: 3000\n\n":
                await asyncio.sleep(0.02)  # Make heartbeat due before replay.
            await send(message)

        await original(self, delayed)

    monkeypatch.setattr(event_stream.EventStreamResponse, "stream_response", slow)
    async with stream(client, application_id, ids[0]) as (_, lines):
        assert await frame(lines) == "retry: 3000\n\n"
        assert await frame(lines) == ": heartbeat\n\n"
        async with asyncio.timeout(2):
            assert [line async for line in lines] == []
    assert revoked.is_set()
    await assert_no_transaction(app)
