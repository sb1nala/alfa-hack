"""B07 fencing, rollback and recovery on a real isolated PostgreSQL 16."""

import asyncio
import copy
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from test_auth_postgres import in_database
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings
from test_submissions_postgres import cancel, complete, path, submit

from app.check_sources import SourceError, validate_response
from app.database import create_engine, create_session_factory
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationSubmission,
    PipelineJob,
    SubmissionChecks,
)
from app.worker import LostLease, Worker

ROOT = Path(__file__).resolve().parents[2]
COMPANIES = json.loads((ROOT / "seed/companies.json").read_text())["companies"]
SOURCE = COMPANIES[0]
INN = SOURCE["company"]["inn"]
CHECKS = {
    stage: {
        "inn": INN,
        **data,
        **(
            {"name": SOURCE["company"]["name"], "legal_form": SOURCE["company"]["legal_form"]}
            if stage == "egrul"
            else {}
        ),
    }
    for stage, data in SOURCE["mocks"].items()
}


def good(request):
    stage = request.url.path.strip("/").split("/")[0]
    return httpx.Response(200, json=CHECKS[stage])


@pytest.fixture
def queued(actors):
    async def isolate(db):
        # Only the random disposable test DB, never a working database.
        await db.execute(
            update(PipelineJob)
            .where(PipelineJob.state.in_(["pending", "running"]))
            .values(state="cancelled", lease_token=None, lease_expires_at=None)
        )

    in_database(actors.settings, isolate)
    response = submit(actors, complete(actors))
    assert response.status_code == 202
    return actors, response.json()


@pytest.fixture
async def rig(queued):
    actors, application = queued
    engine = create_engine(actors.settings)
    sessions = create_session_factory(engine)
    settings = actors.settings.model_copy(
        update={"worker_retry_base": 0.1, "worker_retry_max": 0.2, "worker_poll_interval": 0.05}
    )

    @asynccontextmanager
    async def worker(handler=good, **changes):
        async with httpx.AsyncClient(
            base_url="http://mocks", transport=httpx.MockTransport(handler)
        ) as client:
            yield Worker(sessions, client, settings.model_copy(update=changes))

    async def state():
        async with sessions() as db:
            app = await db.get(Application, UUID(application["id"]))
            job = await db.scalar(select(PipelineJob).where(PipelineJob.application_id == app.id))
            checks = await db.get(SubmissionChecks, job.submission_id)
            events = list(
                await db.scalars(
                    select(ApplicationEvent)
                    .where(ApplicationEvent.application_id == app.id)
                    .order_by(ApplicationEvent.sequence)
                )
            )
            return app, job, checks, events

    try:
        yield SimpleNamespace(
            worker=worker,
            state=state,
            sessions=sessions,
            engine=engine,
            actors=actors,
            application=application,
        )
    finally:
        await engine.dispose()


async def expire(rig, claim):
    async with rig.sessions.begin() as db:
        await db.execute(
            update(PipelineJob)
            .where(PipelineJob.id == claim.job_id)
            .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )


async def due(rig, claim):
    async with rig.sessions.begin() as db:
        await db.execute(
            update(PipelineJob)
            .where(PipelineJob.id == claim.job_id)
            .values(available_at=func.clock_timestamp() - timedelta(seconds=1))
        )


@pytest.mark.anyio
async def test_success_snapshot_revisions_audit_and_decision_handoff(rig):
    async with rig.sessions.begin() as db:
        await db.execute(
            update(Application)
            .where(Application.id == UUID(rig.application["id"]))
            .values(company_snapshot={**SOURCE["company"], "inn": "0000000000"})
        )
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return good(request)

    async with rig.worker(handler) as worker:
        claim = await worker.claim()
        await worker.process(claim)
        await worker.process(claim)
    app, job, checks, events = await rig.state()
    assert (app.status, app.revision, app.input_revision) == ("SCORING", 4, 2)
    assert (job.phase, job.state, job.attempts, job.lease_token) == ("decision", "pending", 0, None)
    assert (
        checks.egrul == CHECKS["egrul"]
        and checks.pb == CHECKS["pb"]
        and checks.bki == CHECKS["bki"]
    )
    assert [(e.previous_status, e.status, e.revision) for e in events[1:]] == [
        ("DRAFT", "SUBMITTED", 2),
        ("SUBMITTED", "CHECKING", 3),
        ("CHECKING", "SCORING", 4),
    ]
    assert [e.sequence for e in events] == [1, 2, 3, 4]
    assert all(e.actor_kind == "system" and e.request_id == claim.token for e in events[2:])
    assert requests == [
        f"http://mocks/egrul/{INN}",
        f"http://mocks/pb/{INN}",
        f"http://mocks/bki/report?inn={INN}",
    ]
    public = rig.actors.first.get(path(rig.application)).json()
    assert public["decision"] is None and "bki" not in json.dumps(public)


@pytest.mark.anyio
async def test_two_workers_and_skip_locked(rig):
    async with rig.worker() as first, rig.worker() as second:
        async with rig.sessions.begin() as db:
            await db.scalar(
                select(PipelineJob)
                .where(PipelineJob.application_id == UUID(rig.application["id"]))
                .with_for_update()
            )
            assert await asyncio.wait_for(first.claim(), 2) is None
        claims = await asyncio.gather(first.claim(), second.claim())
        assert sum(c is not None for c in claims) == 1
        winner = next(c for c in claims if c is not None)
        await asyncio.gather(first.process(winner), second.process(winner))
    app, _, checks, events = await rig.state()
    assert app.status == "SCORING" and checks is not None
    assert sum(e.status == "SCORING" for e in events) == 1


@pytest.mark.anyio
async def test_claim_leaves_other_ready_jobs_available(rig, monkeypatch):
    second_application = submit(rig.actors, complete(rig.actors)).json()
    acquired, release = asyncio.Event(), asyncio.Event()
    async with rig.worker() as first, rig.worker() as second:
        original_transaction = first.transaction

        @asynccontextmanager
        async def hold_before_commit():
            async with original_transaction() as db:
                yield db
                # Keep the first claim's real PostgreSQL locks until worker 2 runs.
                acquired.set()
                await release.wait()

        monkeypatch.setattr(first, "transaction", hold_before_commit)
        first_task = asyncio.create_task(first.claim())
        try:
            await asyncio.wait_for(acquired.wait(), 2)
            second_claim = await asyncio.wait_for(second.claim(), 2)
        finally:
            release.set()
            first_claim = await asyncio.wait_for(first_task, 2)

        assert first_claim is not None and second_claim is not None
        assert first_claim.job_id != second_claim.job_id
        assert {first_claim.application_id, second_claim.application_id} == {
            UUID(rig.application["id"]),
            UUID(second_application["id"]),
        }
        assert first_claim.attempt == second_claim.attempt == 1
        assert await second.claim() is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["timeout", "503", "429", "network", "deadline"])
async def test_retry_then_success(rig, failure):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("sensitive", request=request)
            if failure == "network":
                raise httpx.ConnectError("secret", request=request)
            if failure == "deadline":
                await asyncio.Event().wait()
            return httpx.Response(int(failure))
        return good(request)

    async with rig.worker(
        handler,
        worker_attempt_timeout=1 if failure == "deadline" else 30,
        worker_retry_base=10,
        worker_retry_max=10,
    ) as worker:
        claim = await worker.claim()
        await worker.process(claim)
        app, job, checks, events = await rig.state()
        assert (app.status, app.revision, app.input_revision, job.state) == (
            "CHECKING",
            4,
            2,
            "pending",
        )
        assert job.available_at > events[-1].occurred_at and checks is None
        assert (events[-1].previous_status, events[-1].status) == ("CHECKING", "CHECKING")
        assert await worker.claim() is None
        await due(rig, claim)
        await worker.process(await worker.claim())
    assert (await rig.state())[0].status == "SCORING"


@pytest.mark.anyio
async def test_attempts_exhausted_without_credit_rejection(rig):
    async with rig.worker(lambda r: httpx.Response(503), worker_max_attempts=2) as worker:
        for n in (1, 2):
            claim = await worker.claim()
            assert claim.attempt == n
            await worker.process(claim)
            await due(rig, claim)
        assert await worker.claim() is None
    app, job, checks, events = await rig.state()
    assert app.status == "CHECKING" and job.state == "failed" and checks is None
    assert job.last_error_category == "http_503" and app.revision == 4 and len(events) == 4


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["404", "inn", "json", "missing", "bool", "null", "status"])
async def test_invalid_transport_is_technical_failed(rig, kind):
    def handler(request):
        if kind == "404":
            return httpx.Response(404)
        if kind == "json":
            return httpx.Response(200, content=b"{broken")
        if kind == "null":
            return httpx.Response(200, content=b"null")
        data = copy.deepcopy(CHECKS["egrul"])
        if kind == "inn":
            data["inn"] = "0000000000"
        if kind == "missing":
            del data["status"]
        if kind == "bool":
            data["name"] = True
        if kind == "status":
            data["status"] = "UNKNOWN"
        return httpx.Response(200, json=data)

    async with rig.worker(handler) as worker:
        await worker.process(await worker.claim())
    app, job, checks, _ = await rig.state()
    assert app.status == "CHECKING" and job.state == "failed" and checks is None
    assert job.last_error_category in {"http_404", "invalid_json", "invalid_data"}


@pytest.mark.anyio
async def test_pb_unavailable_is_valid(rig):
    def handler(request):
        if request.url.path.startswith("/pb/"):
            return httpx.Response(
                200, json={**CHECKS["pb"], "available": False, "revenue": None, "expenses": None}
            )
        return good(request)

    async with rig.worker(handler) as worker:
        await worker.process(await worker.claim())
    app, _, checks, _ = await rig.state()
    assert app.status == "SCORING" and checks.pb["available"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("started", [False, True])
async def test_crash_reclaim_and_stale_executor_cannot_write_or_retry(rig, started):
    async with rig.worker() as old, rig.worker() as new:
        first = await old.claim()
        if started:
            await old.start(first)
        await expire(rig, first)
        with pytest.raises(LostLease):
            await old.succeed(first, CHECKS)
        with pytest.raises(LostLease):
            await old.fail(first, SourceError("pb", "network", retryable=True))
        second = await new.claim()
        assert second.token != first.token and second.attempt == 2
        with pytest.raises(LostLease):
            await old.fail(first, SourceError("bki", "network", retryable=True))
        with pytest.raises(LostLease):
            await old.succeed(first, CHECKS)
        await new.process(second)
        await old.process(first)
    app, job, checks, events = await rig.state()
    assert app.status == "SCORING" and job.phase == "decision" and checks is not None
    assert sum(e.previous_status == "SUBMITTED" for e in events) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["cancel", "inputs"])
@pytest.mark.parametrize("upstream_failed", [False, True])
async def test_late_http_after_cancel_or_changed_inputs(rig, action, upstream_failed):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(503) if upstream_failed else good(request)

    async with rig.worker(handler) as worker:
        task = asyncio.create_task(worker.process(await worker.claim()))
        await asyncio.wait_for(entered.wait(), 2)
        async with rig.sessions.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout='500ms'"))
            app = await db.scalar(
                select(Application)
                .where(Application.id == UUID(rig.application["id"]))
                .with_for_update(nowait=True)
            )
            await db.scalar(
                select(PipelineJob)
                .where(PipelineJob.application_id == app.id)
                .with_for_update(nowait=True)
            )
            if action == "inputs":
                app.revision += 1
                app.input_revision = app.revision
        if action == "cancel":
            current = rig.actors.first.get(path(rig.application)).json()
            assert cancel(rig.actors, current).status_code == 200
        release.set()
        await asyncio.wait_for(task, 3)
        assert await worker.claim() is None
    app, job, checks, events = await rig.state()
    assert app.status == ("CANCELLED" if action == "cancel" else "CHECKING")
    assert job.state == "cancelled" and checks is None
    assert all(e.status != "SCORING" for e in events)


@pytest.mark.anyio
@pytest.mark.parametrize("table", ["submission_checks", "application_events", "pipeline_jobs"])
async def test_write_failure_rolls_back_entire_handoff(rig, table):
    async with rig.worker() as worker:
        claim = await worker.claim()
        await worker.start(claim)
        name = "b07_failure_" + uuid4().hex
        async with rig.sessions.begin() as db:
            condition = "NEW.phase = 'decision'" if table == "pipeline_jobs" else "true"
            await db.execute(
                text(
                    f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF {condition} THEN RAISE EXCEPTION 'injected'; END IF; "
                    "RETURN NEW; END $$"
                )
            )
            await db.execute(
                text(
                    f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION {name}()"
                )
            )
        try:
            with pytest.raises(DBAPIError):
                await worker.succeed(claim, CHECKS)
            app, job, checks, events = await rig.state()
            assert (app.status, app.revision, job.phase, job.state) == (
                "CHECKING",
                3,
                "checks",
                "running",
            )
            assert checks is None and len(events) == 3
        finally:
            async with rig.sessions.begin() as db:
                await db.execute(text(f"DROP TRIGGER {name} ON {table}"))
                await db.execute(text(f"DROP FUNCTION {name}()"))
        await expire(rig, claim)
        await worker.process(await worker.claim())
        assert (await rig.state())[0].status == "SCORING"


@pytest.mark.anyio
async def test_real_demo_bki_first_two_503_then_success(rig):
    company = next(c for c in COMPANIES if c["client_login"] == "demo-bki")
    async with rig.sessions.begin() as db:
        submission = await db.scalar(
            select(ApplicationSubmission).where(
                ApplicationSubmission.application_id == UUID(rig.application["id"])
            )
        )
        submission.form_snapshot = {**submission.form_snapshot, "company": company["company"]}
    spec = importlib.util.spec_from_file_location("b07_real_mocks", ROOT / "mocks/app/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.create_app(latency_ms=0)
    async with httpx.AsyncClient(
        base_url="http://mocks", transport=httpx.ASGITransport(app)
    ) as client:
        worker = Worker(rig.sessions, client, rig.actors.settings)
        for n in (1, 2, 3):
            claim = await worker.claim()
            await worker.process(claim)
            await due(rig, claim)
            state = await rig.state()
            assert state[0].status == ("SCORING" if n == 3 else "CHECKING")
            if n < 3:
                assert state[1].last_error_category == "http_503"
        decision_claim = await worker.claim()
        assert decision_claim.phase == "decision"
    assert state[2].bki == {"inn": company["company"]["inn"], **company["mocks"]["bki"]}


@pytest.mark.parametrize("stage", ["egrul", "pb", "bki"])
def test_each_transport_field_required(stage):
    for field in CHECKS[stage]:
        with pytest.raises(SourceError):
            validate_response(stage, {k: v for k, v in CHECKS[stage].items() if k != field}, INN)


@pytest.mark.parametrize(
    "stage,field,value",
    [
        ("pb", "available", "false"),
        ("pb", "mass_address", 0),
        ("pb", "tax_arrears", None),
        ("pb", "revenue", "NaN"),
        ("pb", "expenses", "-1.00"),
        ("pb", "employees", True),
        ("pb", "revenue", 123.45),
        ("bki", "loans", None),
        ("bki", "inquiries_30d", "0"),
        ("bki", "max_overdue_days_24m", -1),
        ("egrul", "registration_date", "2026-02-30"),
        ("egrul", "okved", ""),
    ],
)
def test_invalid_types_and_values(stage, field, value):
    with pytest.raises(SourceError):
        validate_response(stage, {**CHECKS[stage], field: value}, INN)


def test_loan_missing_overdue_cannot_become_zero():
    data = copy.deepcopy(CHECKS["bki"])
    del data["loans"][0]["current_overdue_days"]
    with pytest.raises(SourceError):
        validate_response("bki", data, INN)


@pytest.mark.anyio
async def test_no_open_worker_transaction_during_http(rig):
    from sqlalchemy import event

    pids = set()

    def checkout(connection, record, proxy):
        pids.add(connection.driver_connection.get_server_pid())

    event.listen(rig.engine.sync_engine, "checkout", checkout)
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return good(request)

    observer = create_engine(rig.actors.settings)
    try:
        async with rig.worker(handler) as worker:
            task = asyncio.create_task(worker.process(await worker.claim()))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                async with observer.connect() as connection:
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    activity = (
                        await connection.execute(
                            text(
                                "SELECT pid, xact_start, state FROM pg_stat_activity "
                                "WHERE pid=ANY(:pids)"
                            ),
                            {"pids": list(pids)},
                        )
                    ).all()
                assert activity and all(
                    row.xact_start is None and row.state == "idle" for row in activity
                )
            finally:
                release.set()
                await asyncio.wait_for(task, 3)
    finally:
        event.remove(rig.engine.sync_engine, "checkout", checkout)
        await observer.dispose()


@pytest.mark.anyio
async def test_expiry_during_audit_rolls_back_results_and_transition(rig):
    async with rig.worker() as worker:
        claim = await worker.claim()
        await worker.start(claim)
        name = "b07_delay_" + uuid4().hex
        async with rig.sessions.begin() as db:
            await db.execute(
                text(
                    f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN UPDATE pipeline_jobs "
                    "SET lease_expires_at=clock_timestamp()-interval '1 second' "
                    f"WHERE id='{claim.job_id}'; RETURN NEW; END $$"
                )
            )
            await db.execute(
                text(
                    f"CREATE TRIGGER {name} BEFORE INSERT ON application_events "
                    f"FOR EACH ROW EXECUTE FUNCTION {name}()"
                )
            )
        try:
            with pytest.raises(LostLease):
                await worker.succeed(claim, CHECKS)
        finally:
            async with rig.sessions.begin() as db:
                await db.execute(text(f"DROP TRIGGER {name} ON application_events"))
                await db.execute(text(f"DROP FUNCTION {name}()"))
        app, job, checks, events = await rig.state()
        assert (app.status, app.revision, job.phase) == ("CHECKING", 3, "checks")
        assert checks is None and len(events) == 3
        await expire(rig, claim)
        await worker.process(await worker.claim())
        assert (await rig.state())[0].status == "SCORING"


@pytest.mark.anyio
async def test_supervisor_survives_poll_failure_and_failed_job(rig, monkeypatch, caplog):
    second = submit(rig.actors, complete(rig.actors)).json()
    calls = 0
    stop = asyncio.Event()

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(404)
        if request.url.path == "/bki/report":
            stop.set()
        return good(request)

    async with rig.worker(handler) as worker:
        original_claim = worker.claim
        polls = 0

        async def claim():
            nonlocal polls
            polls += 1
            if polls == 1:
                raise OSError("secret-not-for-logs")
            return await original_claim()

        monkeypatch.setattr(worker, "claim", claim)
        await asyncio.wait_for(worker.run(stop), 5)
    assert (await rig.state())[1].state == "failed"
    async with rig.sessions() as db:
        assert (await db.get(Application, UUID(second["id"]))).status == "SCORING"
    assert "secret-not-for-logs" not in caplog.text


@pytest.mark.parametrize(
    "changes",
    [
        {"worker_lease_seconds": 30},
        {"worker_max_attempts": 0},
        {"worker_retry_base": 60, "worker_retry_max": 30},
        {"worker_http_timeout": float("inf")},
        {"worker_poll_interval": 0},
        {"worker_attempt_timeout": 301},
        {"worker_db_timeout": -1},
    ],
)
def test_invalid_worker_configuration(changes):
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(postgres_password="test-only", **changes)


@pytest.mark.anyio
async def test_operator_requeue_is_atomic_and_cannot_requeue_pending_job(rig):
    from app.requeue import requeue

    async with rig.worker(lambda r: httpx.Response(404)) as worker:
        claim = await worker.claim()
        await worker.process(claim)
    async with rig.sessions.begin() as db:
        await requeue(db, claim.job_id)
    app, job, _, events = await rig.state()
    assert (app.status, app.input_revision, app.revision, job.attempts, job.state) == (
        "CHECKING",
        2,
        4,
        0,
        "pending",
    )
    assert events[-1].audit_payload["previous_error_category"] == "http_404"
    async with rig.sessions.begin() as db:
        with pytest.raises(ValueError):
            await requeue(db, claim.job_id)
    async with rig.worker() as worker:
        await worker.process(await worker.claim())
    async with rig.sessions.begin() as db:
        with pytest.raises(ValueError):
            await requeue(db, claim.job_id)
    assert (await rig.state())[0].status == "SCORING"


@pytest.mark.anyio
@pytest.mark.parametrize("stale", ["cancelled", "inputs"])
async def test_operator_cannot_revive_cancelled_or_stale_job(rig, stale):
    from app.requeue import requeue

    async with rig.worker(lambda r: httpx.Response(404)) as worker:
        claim = await worker.claim()
        await worker.process(claim)
    if stale == "cancelled":
        current = rig.actors.first.get(path(rig.application)).json()
        assert cancel(rig.actors, current).status_code == 200
    else:
        async with rig.sessions.begin() as db:
            app = await db.get(Application, claim.application_id)
            app.revision += 1
            app.input_revision = app.revision
    async with rig.sessions.begin() as db:
        with pytest.raises(ValueError):
            await requeue(db, claim.job_id)


@pytest.mark.parametrize("company", COMPANIES, ids=lambda c: c["client_login"])
def test_all_working_seed_responses_pass_transport_validation(company):
    inn = company["company"]["inn"]
    for stage, payload in company["mocks"].items():
        data = {"inn": inn, **payload}
        if stage == "egrul":
            data.update(
                name=company["company"]["name"], legal_form=company["company"]["legal_form"]
            )
        assert validate_response(stage, data, inn) == data


@pytest.mark.anyio
async def test_crashes_consume_attempt_budget_without_extra_http(rig):
    calls = []

    def handler(request):
        calls.append(request)
        return good(request)

    async with rig.worker(handler, worker_max_attempts=1) as worker:
        claim = await worker.claim()
        await expire(rig, claim)
        await worker.process(await worker.claim())
    app, job, checks, _ = await rig.state()
    assert app.status == "CHECKING" and job.state == "failed" and checks is None
    assert job.last_error_category == "attempts_exhausted" and calls == []


@pytest.mark.anyio
async def test_retry_repeats_all_sources_and_keeps_no_partial_results(rig):
    calls = []

    def handler(request):
        stage = request.url.path.strip("/").split("/")[0]
        calls.append(stage)
        if stage == "bki" and calls.count("bki") == 1:
            return httpx.Response(503)
        return good(request)

    async with rig.worker(handler) as worker:
        first = await worker.claim()
        await worker.process(first)
        assert (await rig.state())[2] is None
        await due(rig, first)
        await worker.process(await worker.claim())
    assert calls == ["egrul", "pb", "bki"] * 2
    assert (await rig.state())[0].status == "SCORING"


@pytest.mark.anyio
async def test_database_timeout_is_not_a_source_retry_or_success(rig, monkeypatch):
    async def timeout(*args):
        raise TimeoutError("database-sensitive-detail")

    async with rig.worker() as worker:
        claim = await worker.claim()
        monkeypatch.setattr(worker, "succeed", timeout)
        with pytest.raises(TimeoutError):
            await worker.process(claim)
    app, job, checks, events = await rig.state()
    assert (app.status, app.revision, job.state, job.phase) == ("CHECKING", 3, "running", "checks")
    assert checks is None and len(events) == 3
