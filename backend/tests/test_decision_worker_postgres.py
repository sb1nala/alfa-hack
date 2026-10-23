"""B08 real decide(), PostgreSQL transactions/concurrency, existing public projection."""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_auth_postgres import in_database
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings
from test_submissions_postgres import cancel, complete, path, submit
from test_worker_postgres import COMPANIES, due, expire
from test_worker_postgres import rig as rig

from app import decision_adapter
from app.check_sources import SourceError
from app.decision import decide
from app.decision.policy import POLICY_VERSION
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationSubmission,
    Decision,
    Offer,
    PipelineJob,
    Product,
    SubmissionChecks,
)
from app.requeue import requeue
from app.worker import LostLease
from tests.decision.helpers import build_inputs


@pytest.fixture
def company_key():
    return "approve"


def submission_form(actors, company):
    fields = company["application"]
    product = next(
        p
        for p in actors.first.get("/api/products").json()["items"]
        if p["code"] == fields["product_code"]
    )
    return {
        "company": company["company"],
        "product_id": product["id"],
        **{k: v for k, v in fields.items() if k != "product_code"},
    }


@pytest.fixture
def queued(actors, company_key):
    async def isolate(db):
        await db.execute(
            update(PipelineJob)
            .where(PipelineJob.state.in_(["pending", "running"]))
            .values(state="cancelled", lease_token=None, lease_expires_at=None)
        )

    in_database(actors.settings, isolate)
    company = next(c for c in COMPANIES if c["key"] == company_key)
    response = submit(actors, complete(actors, **submission_form(actors, company)))
    assert response.status_code == 202, response.text
    return actors, response.json()


@pytest.fixture
async def ready(rig, company_key):
    company = next(c for c in COMPANIES if c["key"] == company_key)

    def handler(request):
        stage = request.url.path.strip("/").split("/")[0]
        return httpx.Response(
            200,
            json={
                "inn": company["company"]["inn"],
                **company["mocks"][stage],
                **(
                    {
                        "name": company["company"]["name"],
                        "legal_form": company["company"]["legal_form"],
                    }
                    if stage == "egrul"
                    else {}
                ),
            },
        )

    async with rig.worker(handler) as worker:
        await worker.process(await worker.claim())
    assert (await rig.state())[0].status == "SCORING"
    rig.company, rig.handler = company, handler
    return rig


def no_http(request):
    pytest.fail("decision must never contact mocks")


async def history(rig):
    async with rig.sessions() as db:
        identity = UUID(rig.application["id"])
        return (
            list(await db.scalars(select(Decision).where(Decision.application_id == identity))),
            list(await db.scalars(select(Offer).where(Offer.application_id == identity))),
        )


async def saved_inputs(db, rig):
    job = await db.scalar(
        select(PipelineJob).where(PipelineJob.application_id == UUID(rig.application["id"]))
    )
    return (
        job,
        await db.get(ApplicationSubmission, job.submission_id),
        await db.get(SubmissionChecks, job.submission_id),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", [c["key"] for c in COMPANIES])
async def test_real_decisions_all_outcomes_and_three_counteroffers(ready):
    rig = ready
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        assert claim.phase == "decision" and claim.attempt == 1
        submission, checks = await worker.load_decision(claim)
        expected = decide(
            *build_inputs(rig.company, submission.submitted_at.astimezone(UTC).date())
        )
        await worker.process(claim)
        await worker.process(claim)
        assert await worker.claim() is None
    app, job, stored_checks, events = await rig.state()
    decisions, offers = await history(rig)
    assert app.status == expected.outcome == rig.company["expected_outcome"]
    assert app.revision == 5 and app.input_revision == submission.input_revision == 2
    assert (job.phase, job.state, job.attempts) == ("decision", "completed", 1)
    assert job.lease_token is job.lease_expires_at is None
    assert len(decisions) == 1 and len(events) == 5
    row, data = decisions[0], decision_adapter.result_snapshot(expected)
    assert row.risk_result == data
    assert row.public_reasons == data["reasons"] and row.public_explanation == expected.explanation
    assert row.required_documents == data["required_documents"]
    assert row.scoring_factors == data["scoring_factors"]
    assert row.analytical_explanation == expected.analytical_explanation
    assert row.source == "automatic" and row.author_id is None
    assert row.submission_id == submission.id and row.input_revision == 2 and row.revision == 5
    assert row.policy_version == POLICY_VERSION and app.current_decision_id == row.id
    assert stored_checks.bki == checks.bki
    assert events[-1].audit_payload == {
        "job_id": str(claim.job_id),
        "submission_id": str(claim.submission_id),
        "attempt": 1,
        "phase": "decision",
        "decision_id": str(row.id),
    }
    public = rig.actors.first.get(path(rig.application))
    assert public.status_code == 200
    public = public.json()["decision"]
    assert public["explanation"] == data["explanation"] and public["reasons"] == data["reasons"]
    assert public["required_documents"] == data["required_documents"]
    assert set(public) == {
        "id",
        "created_at",
        "outcome",
        "reasons",
        "explanation",
        "required_documents",
    } | ({"offer"} if expected.offer else set())
    bank = rig.actors.bank.get(f"/api/underwriter/applications/{rig.application['id']}")
    assert bank.status_code == 200, bank.text
    [bank_decision] = bank.json()["decisions"]
    assert bank_decision["review_reason"] == data["review_reason"]
    if expected.outcome != "MANUAL_REVIEW":
        assert bank_decision["review_reason"] is None
    if expected.offer:
        assert len(offers) == 1
        offer = offers[0]
        async with rig.sessions() as db:
            product = await db.get(Product, offer.product_id)
        assert product.code == expected.offer.product_code == offer.product_snapshot["code"]
        assert offer.product_snapshot["policy"]["policy_version"] == POLICY_VERSION
        assert (
            offer.amount == expected.offer.amount
            and offer.term_months == expected.offer.term_months
        )
        assert offer.expires_at is None and public["offer"]["expires_at"] is None
        assert public["offer"]["terms"] == {
            "product_id": str(product.id),
            "amount": str(expected.offer.amount),
            "term_months": expected.offer.term_months,
            "annual_rate_percent": expected.offer.annual_rate_percent,
        }
        if rig.company["key"] == "counter_product":
            assert str(offer.product_id) != rig.application["form"]["product_id"]
    else:
        assert offers == []


@pytest.mark.anyio
async def test_two_ready_jobs_skip_locked_limit_one_and_duplicate_execution(ready, monkeypatch):
    rig = ready
    async with rig.sessions.begin() as db:
        job, _, _ = await saved_inputs(db, rig)
        job.available_at = datetime.now(UTC) + timedelta(hours=1)
    second_app = submit(
        rig.actors, complete(rig.actors, **submission_form(rig.actors, rig.company))
    ).json()
    async with rig.worker(rig.handler) as first, rig.worker(no_http) as second:
        await first.process(await first.claim())
        async with rig.sessions.begin() as db:
            job, _, _ = await saved_inputs(db, rig)
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
        acquired, release = asyncio.Event(), asyncio.Event()
        original = first.transaction

        @asynccontextmanager
        async def hold():
            async with original() as db:
                yield db
                acquired.set()
                await release.wait()

        monkeypatch.setattr(first, "transaction", hold)
        task = asyncio.create_task(first.claim())
        try:
            await asyncio.wait_for(acquired.wait(), 2)
            second_claim = await asyncio.wait_for(second.claim(), 2)
        finally:
            release.set()
            first_claim = await asyncio.wait_for(task, 2)
        monkeypatch.setattr(first, "transaction", original)
        assert first_claim.phase == second_claim.phase == "decision"
        assert {first_claim.application_id, second_claim.application_id} == {
            UUID(rig.application["id"]),
            UUID(second_app["id"]),
        }
        await asyncio.gather(
            first.process(first_claim), second.process(first_claim), second.process(second_claim)
        )
        assert await first.claim() is None
    async with rig.sessions() as db:
        for app_id in (first_claim.application_id, second_claim.application_id):
            for model in (Decision, Offer):
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.application_id == app_id)
                    )
                    == 1
                )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ApplicationEvent)
                    .where(
                        ApplicationEvent.application_id == app_id,
                        ApplicationEvent.status == "APPROVED",
                    )
                )
                == 1
            )


@pytest.mark.anyio
@pytest.mark.parametrize("calculated", [False, True])
async def test_crash_recovery_and_old_lease_cannot_write_or_fail(ready, calculated):
    rig = ready
    async with rig.worker(no_http) as old, rig.worker(no_http) as new:
        first = await old.claim()
        if calculated:
            result = decision_adapter.calculate(*(await old.load_decision(first)))
        await expire(rig, first)
        if calculated:
            with pytest.raises(LostLease):
                await old.save_decision(first, result)
        second = await new.claim()
        assert second.phase == "decision" and second.attempt == 2 and second.token != first.token
        if not calculated:
            result = decision_adapter.calculate(*(await new.load_decision(second)))
        with pytest.raises(LostLease):
            await old.fail(first, SourceError("decision", "attempt_timeout", retryable=True))
        with pytest.raises(LostLease):
            await old.save_decision(first, result)
        await new.process(second)
        await old.process(first)
    assert len((await history(rig))[0]) == len((await history(rig))[1]) == 1
    assert (await rig.state())[1].state == "completed"


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["cancel", "inputs", "phase", "submission", "token", "expired"])
async def test_late_result_is_fenced(ready, action):
    rig = ready
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        result = decision_adapter.calculate(*(await worker.load_decision(claim)))
        if action == "cancel":
            current = rig.actors.first.get(path(rig.application)).json()
            assert cancel(rig.actors, current).status_code == 200
        elif action == "expired":
            await expire(rig, claim)
        elif action == "inputs":
            async with rig.sessions.begin() as db:
                app = await db.get(Application, claim.application_id)
                app.revision += 1
                app.input_revision = app.revision
        elif action == "submission":
            claim = replace(claim, submission_id=uuid4())
        else:
            async with rig.sessions.begin() as db:
                job = await db.get(PipelineJob, claim.job_id)
                if action == "phase":
                    job.phase = "checks"
                else:
                    job.lease_token = uuid4()
        if action == "inputs":
            await worker.save_decision(claim, result)
        else:
            with pytest.raises(LostLease):
                await worker.save_decision(claim, result)
    assert await history(rig) == ([], [])
    app, _, _, events = await rig.state()
    assert app.current_decision_id is None and all(e.status != "APPROVED" for e in events)


@asynccontextmanager
async def trigger(rig, table, body):
    name = "b08_inject_" + uuid4().hex
    async with rig.sessions.begin() as db:
        await db.execute(
            text(
                f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN {body} END $$"
            )
        )
        await db.execute(
            text(
                f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
        )
    try:
        yield
    finally:
        async with rig.sessions.begin() as db:
            await db.execute(text(f"DROP TRIGGER {name} ON {table}"))
            await db.execute(text(f"DROP FUNCTION {name}()"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "table", ["decisions", "offers", "applications", "application_events", "pipeline_jobs"]
)
async def test_partial_persistence_rolls_back_and_recovers(ready, table):
    rig = ready
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        result = decision_adapter.calculate(*(await worker.load_decision(claim)))
        async with trigger(rig, table, "RAISE EXCEPTION 'injected';"):
            with pytest.raises(DBAPIError):
                await worker.save_decision(claim, result)
        assert await history(rig) == ([], [])
        app, job, _, events = await rig.state()
        assert (app.status, app.revision, app.current_decision_id) == ("SCORING", 4, None)
        assert job.state == "running" and len(events) == 4
        await expire(rig, claim)
        await worker.process(await worker.claim())
    assert len((await history(rig))[0]) == len((await history(rig))[1]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["expiry", "token", "phase", "inputs", "status"])
async def test_final_fence_after_audit_rolls_back_everything(ready, mutation):
    rig = ready
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        result = decision_adapter.calculate(*(await worker.load_decision(claim)))
        assignment = {
            "expiry": "lease_expires_at=clock_timestamp()-interval '1 second'",
            "token": f"lease_token='{uuid4()}'",
            "phase": "phase='checks'",
            "inputs": "phase='decision'",
            "status": "phase='decision'",
        }[mutation]
        body = f"UPDATE pipeline_jobs SET {assignment} WHERE id='{claim.job_id}'; RETURN NEW;"
        if mutation == "inputs":
            body = (
                "UPDATE applications SET input_revision=revision "
                f"WHERE id='{claim.application_id}'; RETURN NEW;"
            )
        elif mutation == "status":
            body = (
                "UPDATE applications SET status='CANCELLED' "
                f"WHERE id='{claim.application_id}'; RETURN NEW;"
            )
        async with trigger(rig, "application_events", body):
            with pytest.raises(LostLease):
                await worker.save_decision(claim, result)
    assert await history(rig) == ([], [])
    app, job, _, events = await rig.state()
    assert app.status == "SCORING" and app.revision == 4 and len(events) == 4
    assert job.phase == "decision" and job.lease_token == claim.token


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,category",
    [
        ("purpose", "unsupported_purpose"),
        ("policy", "incompatible_policy"),
        ("policy_drift", "incompatible_policy"),
        ("schema", "unsupported_checks_schema"),
        ("missing_checks", "missing_checks"),
        ("amount", "invalid_inputs"),
        ("term", "invalid_inputs"),
        ("legal_form", "invalid_inputs"),
        ("product", "unsupported_product"),
        ("report", "invalid_data"),
    ],
)
async def test_incompatible_inputs_are_technical_failure(ready, kind, category):
    rig = ready
    async with rig.sessions.begin() as db:
        _, sub, checks = await saved_inputs(db, rig)
        form, product = copy.deepcopy(sub.form_snapshot), copy.deepcopy(sub.product_snapshot)
        if kind == "purpose":
            form["purpose"] = "  Купить оборудование  "
        if kind == "amount":
            form["amount"] = "NaN"
        if kind == "term":
            form["term_months"] = True
        if kind == "legal_form":
            form["company"]["legal_form"] = "UNKNOWN"
        if kind == "policy":
            product["policy"]["policy_version"] = "future-policy"
        if kind == "policy_drift":
            product["policy"]["max_amount"] = "1.00"
        if kind == "product":
            product["code"] = "mortgage"
        if kind == "schema":
            checks.schema_version = 99
        if kind == "missing_checks":
            await db.delete(checks)
        if kind == "report":
            checks.pb = {**checks.pb, "director_disqualified": "false"}
        sub.form_snapshot, sub.product_snapshot = form, product
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
        assert await worker.claim() is None
    app, job, _, events = await rig.state()
    assert app.status == "SCORING" and app.current_decision_id is None and len(events) == 4
    assert job.state == "failed" and job.last_error_category == category
    assert job.lease_token is job.lease_expires_at is None and await history(rig) == ([], [])
    if kind == "purpose":
        async with rig.sessions() as db:
            assert (await db.get(ApplicationSubmission, job.submission_id)).form_snapshot[
                "purpose"
            ] == "  Купить оборудование  "


@pytest.mark.anyio
async def test_none_revenue_is_real_needs_info(ready):
    rig = ready
    async with rig.sessions.begin() as db:
        _, sub, _ = await saved_inputs(db, rig)
        sub.form_snapshot = {**sub.form_snapshot, "annual_revenue": None}
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
    decisions, offers = await history(rig)
    assert offers == [] and decisions[0].outcome == "NEEDS_INFO"
    assert [d["code"] for d in decisions[0].required_documents] == ["annual_revenue"]


@pytest.mark.anyio
async def test_snapshots_and_utc_submission_date_ignore_current_data(ready):
    rig = ready
    async with rig.sessions.begin() as db:
        job, sub, checks = await saved_inputs(db, rig)
        sub.submitted_at = datetime(2026, 10, 20, 23, 30, tzinfo=timezone(-timedelta(hours=3)))
        checks.egrul = {**checks.egrul, "registration_date": "2026-07-21"}
        app = await db.get(Application, job.application_id)
        app.amount, app.term_months, app.purpose, app.annual_revenue = 1, 1, "current text", None
        app.company_snapshot = {**app.company_snapshot, "legal_form": "IP"}
        app.product_snapshot = {"code": "mortgage"}
        product = await db.get(Product, UUID(sub.product_snapshot["id"]))
        original = product.policy_snapshot
        product.policy_snapshot = {"policy_version": "new-policy"}
    try:
        async with rig.worker(no_http) as worker:
            claim = await worker.claim()
            inputs = await worker.load_decision(claim)
            application = decision_adapter.build_inputs(*inputs)[0]
            assert application.submitted_on.isoformat() == "2026-10-21"
            assert application.document_types == frozenset() and application.legal_form == "OOO"
            assert str(application.amount) == rig.company["application"]["amount"]
            await worker.save_decision(claim, decide(*decision_adapter.build_inputs(*inputs)))
        decisions, offers = await history(rig)
        assert decisions[0].outcome != "REJECTED"
        assert offers[0].product_snapshot == inputs[0].product_snapshot
    finally:
        async with rig.sessions.begin() as db:
            (await db.get(Product, product.id)).policy_snapshot = original


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["counter_product"])
@pytest.mark.parametrize("condition", ["incompatible", "inactive"])
async def test_alternative_product_must_be_available_and_compatible(ready, condition):
    rig = ready
    async with rig.sessions.begin() as db:
        product = await db.scalar(select(Product).where(Product.code == "investment"))
        original, active = product.policy_snapshot, product.active
        if condition == "incompatible":
            product.policy_snapshot = {**original, "policy_version": "future"}
        else:
            product.active = False
    try:
        async with rig.worker(no_http) as worker:
            await worker.process(await worker.claim())
        app, job, _, _ = await rig.state()
        assert app.status == "SCORING" and job.state == "failed"
        assert job.last_error_category == (
            "incompatible_policy" if condition == "incompatible" else "offered_product_unavailable"
        )
        assert await history(rig) == ([], [])
    finally:
        async with rig.sessions.begin() as db:
            product = await db.get(Product, product.id)
            product.policy_snapshot, product.active = original, active


@pytest.mark.anyio
async def test_failed_decision_requeue_audits_and_preserves_inputs_and_checks(ready, monkeypatch):
    rig = ready
    original = decision_adapter.calculate

    def broken(*args):
        raise SourceError("decision", "calculation_error", retryable=False)

    monkeypatch.setattr(decision_adapter, "calculate", broken)
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        await worker.process(claim)
    monkeypatch.setattr(decision_adapter, "calculate", original)
    async with rig.sessions.begin() as db:
        await requeue(db, claim.job_id)
    app, job, checks, events = await rig.state()
    assert (app.status, app.revision, app.input_revision, job.attempts, job.state) == (
        "SCORING",
        5,
        2,
        0,
        "pending",
    )
    assert checks is not None and events[-1].event_type == "application_updated"
    assert events[-1].audit_payload["phase"] == "decision"
    assert events[-1].audit_payload["previous_error_category"] == "calculation_error"
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
    assert (await history(rig))[0][0].revision == 6
    async with rig.sessions.begin() as db:
        with pytest.raises(ValueError):
            await requeue(db, claim.job_id)


@pytest.mark.anyio
async def test_decision_crashes_consume_budget_without_calculation(ready, monkeypatch):
    rig = ready

    def forbidden(*args):
        pytest.fail("exhausted job must not calculate")

    monkeypatch.setattr(decision_adapter, "calculate", forbidden)
    async with rig.worker(no_http, worker_max_attempts=1) as worker:
        claim = await worker.claim()
        await expire(rig, claim)
        await worker.process(await worker.claim())
    app, job, _, _ = await rig.state()
    assert app.status == "SCORING" and job.state == "failed"
    assert job.last_error_category == "attempts_exhausted" and await history(rig) == ([], [])


@pytest.mark.anyio
async def test_calculation_exception_is_technical_and_diagnostic(ready, monkeypatch):
    rig = ready

    def broken(*args):
        raise ValueError("private risk details")

    monkeypatch.setattr(decision_adapter, "decide", broken)
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
    app, job, _, _ = await rig.state()
    assert app.status == "SCORING" and job.state == "failed"
    assert job.last_error_category == "calculation_error" and await history(rig) == ([], [])


@pytest.mark.anyio
async def test_decision_timeout_has_bounded_retry_in_same_phase(ready, monkeypatch):
    rig = ready

    def timeout(*args):
        raise TimeoutError("private exception")

    monkeypatch.setattr(decision_adapter, "calculate", timeout)
    async with rig.worker(no_http, worker_max_attempts=2) as worker:
        for attempt in (1, 2):
            claim = await worker.claim()
            await worker.process(claim)
            await due(rig, claim)
            app, job, _, _ = await rig.state()
            assert app.status == "SCORING" and job.phase == "decision"
            assert job.state == ("pending" if attempt == 1 else "failed")
    assert job.last_error_category == "attempt_timeout"


@pytest.mark.anyio
async def test_submission_unique_constraint_prevents_second_automatic_decision(ready):
    rig = ready
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
    row = (await history(rig))[0][0]
    async with rig.sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                db.add(
                    Decision(
                        application_id=row.application_id,
                        submission_id=row.submission_id,
                        input_revision=2,
                        revision=6,
                        outcome="REJECTED",
                        source="automatic",
                        public_reasons=[],
                        public_explanation="duplicate",
                        required_documents=[],
                        scoring_factors=[],
                        analytical_explanation="duplicate",
                    )
                )
                await db.flush()


@pytest.mark.anyio
async def test_real_calculation_keeps_no_transaction_and_cancel_wins(ready, monkeypatch):
    import threading

    from sqlalchemy import event

    rig = ready
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = decision_adapter.calculate
    pids = set()

    def checkout(connection, record, proxy):
        pids.add(connection.driver_connection.get_server_pid())

    def calculate(*inputs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(*inputs)

    event.listen(rig.engine.sync_engine, "checkout", checkout)
    monkeypatch.setattr(decision_adapter, "calculate", calculate)
    try:
        async with rig.worker(no_http) as worker:
            task = asyncio.create_task(worker.process(await worker.claim()))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                async with rig.sessions.begin() as db:
                    rows = (
                        await db.execute(
                            text(
                                "SELECT xact_start,state FROM pg_stat_activity "
                                "WHERE pid=ANY(:pids) "
                                "AND pid<>pg_backend_pid()"
                            ),
                            {"pids": list(pids)},
                        )
                    ).all()
                    assert all(row.xact_start is None and row.state == "idle" for row in rows)
                    await db.scalar(
                        select(Application)
                        .where(Application.id == UUID(rig.application["id"]))
                        .with_for_update(nowait=True)
                    )
                    await db.scalar(
                        select(PipelineJob)
                        .where(PipelineJob.application_id == UUID(rig.application["id"]))
                        .with_for_update(nowait=True)
                    )
                current = rig.actors.first.get(path(rig.application)).json()
                assert cancel(rig.actors, current).status_code == 200
            finally:
                release.set()
                await asyncio.wait_for(task, 3)
    finally:
        event.remove(rig.engine.sync_engine, "checkout", checkout)
    assert await history(rig) == ([], [])
    assert (await rig.state())[0].status == "CANCELLED"


@pytest.mark.anyio
@pytest.mark.parametrize("stale", ["cancelled", "inputs"])
async def test_failed_decision_requeue_cannot_revive_stale_submission(ready, monkeypatch, stale):
    rig = ready

    def broken(*args):
        raise SourceError("decision", "calculation_error", retryable=False)

    monkeypatch.setattr(decision_adapter, "calculate", broken)
    async with rig.worker(no_http) as worker:
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


@pytest.mark.anyio
async def test_requeue_audit_failure_rolls_back(ready, monkeypatch):
    rig = ready

    def broken(*args):
        raise SourceError("decision", "calculation_error", retryable=False)

    monkeypatch.setattr(decision_adapter, "calculate", broken)
    async with rig.worker(no_http) as worker:
        claim = await worker.claim()
        await worker.process(claim)
    async with trigger(rig, "application_events", "RAISE EXCEPTION 'injected';"):
        with pytest.raises(DBAPIError):
            async with rig.sessions.begin() as db:
                await requeue(db, claim.job_id)
    app, job, _, events = await rig.state()
    assert app.revision == 4 and len(events) == 4 and job.state == "failed"
    assert job.attempts == 1 and job.last_error_category == "calculation_error"


@pytest.mark.anyio
async def test_invalid_public_result_rolls_back_and_is_technical_failure(ready, monkeypatch):
    rig = ready
    original = decision_adapter.calculate

    def invalid(*inputs):
        return replace(original(*inputs), explanation="")

    monkeypatch.setattr(decision_adapter, "calculate", invalid)
    async with rig.worker(no_http) as worker:
        await worker.process(await worker.claim())
    app, job, _, events = await rig.state()
    assert app.status == "SCORING" and app.revision == 4 and len(events) == 4
    assert job.state == "failed" and job.last_error_category == "invalid_result"
    assert await history(rig) == ([], [])
