"""B06 transactional commands, concurrency and B07 queue contract on PostgreSQL 16."""

import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from test_auth_postgres import ORIGIN, csrf, headers, in_database, login
from test_drafts_postgres import COMPANY, create
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings

from app.main import create_app
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationSubmission,
    Decision,
    Offer,
    PipelineJob,
    Product,
    SubmitIdempotency,
)
from app.schemas.common import ApplicationStatus
from app.submissions import job_applicable
from app.transitions import (
    CANCELLABLE,
    TRANSITIONS,
    append_event,
    initiator_allowed,
    lock_application,
    transition,
)


def complete(actors, **changes):
    product = actors.first.get("/api/products").json()["items"][0]
    return create(
        actors,
        {
            "company": COMPANY,
            "product_id": product["id"],
            "amount": "0.01",
            "term_months": 1,
            "purpose": "Свободная цель",
            **changes,
        },
    )


def path(application):
    return "/api/applications/" + application["id"]


def submit(actors, application, *, key=None, body=None, client=None, h=None):
    return (client or actors.first).post(
        path(application) + "/submit",
        json=body or {"expected_revision": application["revision"], "consent_to_checks": True},
        headers={**(h or actors.h), "Idempotency-Key": str(key or uuid4())},
    )


def cancel(actors, application, *, body=None):
    return actors.first.post(
        path(application) + "/cancel",
        headers=actors.h,
        json=body or {"expected_revision": application["revision"]},
    )


def records(actors, application):
    async def read(db):
        identity = UUID(application["id"])
        app = await db.get(Application, identity)
        events = list(
            await db.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == identity)
                .order_by(ApplicationEvent.sequence)
            )
        )
        submissions = list(
            await db.scalars(
                select(ApplicationSubmission).where(
                    ApplicationSubmission.application_id == identity
                )
            )
        )
        jobs = list(
            await db.scalars(select(PipelineJob).where(PipelineJob.application_id == identity))
        )
        keys = list(
            await db.scalars(
                select(SubmitIdempotency).where(SubmitIdempotency.application_id == identity)
            )
        )
        return app, events, submissions, jobs, keys

    return in_database(actors.settings, read)


def assert_untouched(actors, application):
    app, events, submissions, jobs, keys = records(actors, application)
    assert (app.status, app.revision, app.input_revision) == ("DRAFT", 1, None)
    assert len(events) == 1 and not submissions and not jobs and not keys


def test_submit_atomic_snapshot_and_public_response(actors):
    application = complete(actors)
    key = uuid4()
    started = datetime.now(UTC)
    response = submit(actors, application, key=key)
    assert response.status_code == 202, response.text
    result = response.json()
    assert (result["status"], result["revision"], result["allowed_actions"]) == (
        "SUBMITTED",
        2,
        ["cancel"],
    )
    assert result["updated_at"] > application["updated_at"]
    assert result["form"] == application["form"]  # no revenue, below product limits, free text
    app, events, submissions, jobs, keys = records(actors, application)
    assert [(e.sequence, e.revision) for e in events] == [(1, 1), (2, 2)]
    event = events[1]
    assert (event.event_type, event.previous_status, event.status) == (
        "status_changed",
        "DRAFT",
        "SUBMITTED",
    )
    assert event.actor_kind == "client" and str(event.actor_user_id) == result["owner_id"]
    assert str(event.request_id) == response.headers["X-Request-ID"]
    assert event.audit_payload == {"consent_to_checks": True}
    assert len(submissions) == len(jobs) == len(keys) == 1
    snapshot, job, saved = submissions[0], jobs[0], keys[0]
    assert snapshot.input_revision == app.input_revision == 2
    assert started <= snapshot.submitted_at <= datetime.now(UTC)
    assert snapshot.submitted_at == app.updated_at == event.occurred_at
    assert snapshot.consent_to_checks is True
    assert snapshot.form_snapshot == result["form"]
    assert snapshot.product_snapshot == app.product_snapshot
    assert job.submission_id == snapshot.id and job.kind == "application_pipeline"
    assert job.state == "pending" and job.attempts == 0 and job.lease_token is None
    assert job_applicable(app, snapshot, job)
    assert saved.response_body == result and saved.response_status == 202 and saved.key == key
    assert saved.expires_at - saved.created_at >= timedelta(hours=24)
    for secret in (
        "policy_snapshot",
        "audit_payload",
        "input_revision",
        "lease_token",
        "consent_to_checks",
    ):
        assert secret not in response.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("company", None),
        ("product_id", None),
        ("amount", None),
        ("term_months", None),
        ("purpose", None),
        ("purpose", "  \t "),
    ],
)
def test_incomplete_submit_leaves_no_writes(actors, field, value):
    application = complete(actors, **{field: value})
    response = submit(actors, application)
    assert response.status_code == 422
    assert response.json()["field_errors"][0]["field"] == "body." + field
    assert_untouched(actors, application)


@pytest.mark.parametrize(
    "body", [{"expected_revision": 1}, {"expected_revision": 1, "consent_to_checks": False}]
)
def test_consent_required(actors, body):
    application = complete(actors)
    assert submit(actors, application, body=body).status_code == 422
    assert_untouched(actors, application)


def test_product_archived_since_draft(actors):
    async def make_product(db):
        product = Product(code=uuid4().hex, name="Тест", description="Тест", purposes=[])
        db.add(product)
        await db.flush()
        return str(product.id)

    product_id = in_database(actors.settings, make_product)
    application = complete(actors, product_id=product_id)

    async def archive(db):
        await db.execute(update(Product).where(Product.id == UUID(product_id)).values(active=False))

    in_database(actors.settings, archive)
    assert submit(actors, application).status_code == 422
    assert_untouched(actors, application)


@pytest.mark.parametrize("operation", ["submit", "cancel"])
def test_command_access_and_mutation_guards(actors, operation):
    application = complete(actors)
    url = path(application) + "/" + operation
    body = {"expected_revision": 1}
    h = {**actors.h}
    if operation == "submit":
        body["consent_to_checks"] = True
        h["Idempotency-Key"] = str(uuid4())
    for client, code in [(actors.second, 404), (actors.bank, 403)]:
        assert (
            client.post(url, json=body, headers={**h, **headers(csrf(client))}).status_code == code
        )
    with TestClient(create_app(actors.settings), base_url=ORIGIN) as anonymous:
        assert anonymous.post(url, json=body, headers=h).status_code == 401
    for invalid in [
        {k: v for k, v in h.items() if k != "Origin"},
        {**h, "Origin": "https://evil.test"},
        {k: v for k, v in h.items() if k != "X-CSRF-Token"},
        {**h, "X-CSRF-Token": "wrong"},
    ]:
        assert actors.first.post(url, json=body, headers=invalid).status_code == 403
    assert_untouched(actors, application)


@pytest.mark.parametrize("key", [None, "bad-uuid"])
def test_submit_requires_uuid_key(actors, key):
    application = complete(actors)
    h = {**actors.h}
    if key is not None:
        h["Idempotency-Key"] = key
    assert (
        actors.first.post(
            path(application) + "/submit",
            headers=h,
            json={"expected_revision": 1, "consent_to_checks": True},
        ).status_code
        == 422
    )
    assert_untouched(actors, application)


def test_replay_original_response_after_cancel_and_restart(actors):
    application = complete(actors)
    key = uuid4()
    response = submit(actors, application, key=key)
    assert response.status_code == 202
    result = response.json()
    # Same validated request with different whitespace/key order.
    replay = actors.first.post(
        path(application) + "/submit",
        headers={**actors.h, "Idempotency-Key": str(key), "Content-Type": "application/json"},
        content=' { "consent_to_checks" : true, "expected_revision": 1 } ',
    )
    assert replay.status_code == 202 and replay.json() == result
    cancelled = cancel(actors, result, body={"expected_revision": 2, "reason": "Передумал"})
    assert cancelled.status_code == 200
    assert cancelled.json()["allowed_actions"] == []
    with TestClient(create_app(actors.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(actors.first.cookies)
        repeated = submit(actors, application, key=key, client=restarted)
        assert repeated.status_code == 202 and repeated.json() == result
        # Replay must still pass authentication, ownership, Origin and CSRF.
        assert (
            restarted.post(
                path(application) + "/submit",
                json={"expected_revision": 1, "consent_to_checks": True},
                headers={"Idempotency-Key": str(key)},
            ).status_code
            == 403
        )
    assert (
        submit(
            actors, application, key=key, body={"expected_revision": 2, "consent_to_checks": True}
        ).status_code
        == 409
    )
    assert submit(actors, application).status_code == 409
    app, events, submissions, jobs, keys = records(actors, application)
    assert app.revision == 3 and app.status == "CANCELLED"
    assert [(e.sequence, e.revision) for e in events] == [(1, 1), (2, 2), (3, 3)]
    assert events[-1].audit_payload == {"reason": "Передумал"}
    assert "Передумал" not in events[-1].public_message
    assert jobs[0].state == "cancelled" and not job_applicable(app, submissions[0], jobs[0])
    assert len(keys) == len(jobs) == len(submissions) == 1


@pytest.mark.parametrize("status", list(ApplicationStatus))
def test_submit_wrong_status_or_stale_revision(actors, status):
    application = complete(actors)

    async def arrange(db):
        await db.execute(
            update(Application)
            .where(Application.id == UUID(application["id"]))
            .values(status=status.value, revision=2)
        )

    in_database(actors.settings, arrange)
    assert submit(actors, application).status_code == 409
    if status != ApplicationStatus.DRAFT:
        assert submit(actors, {**application, "revision": 2}).status_code == 409
    _, events, submissions, jobs, keys = records(actors, application)
    assert len(events) == 1 and not submissions and not jobs and not keys


@pytest.mark.parametrize("status", list(ApplicationStatus))
def test_cancel_all_states(actors, status):
    application = complete(actors)

    async def arrange(db):
        await db.execute(
            update(Application)
            .where(Application.id == UUID(application["id"]))
            .values(status=status.value)
        )

    in_database(actors.settings, arrange)
    response = cancel(actors, application)
    app, events, _, _, _ = records(actors, application)
    if status.value in CANCELLABLE:
        assert response.status_code == 200
        assert (app.status, app.revision) == ("CANCELLED", 2)
        assert response.json()["allowed_actions"] == []
        assert events[-1].previous_status == status.value
        assert events[-1].event_type == "status_changed" and events[-1].revision == 2
        assert str(events[-1].request_id) == response.headers["X-Request-ID"]
    else:
        assert response.status_code == 409
        assert app.revision == 1 and len(events) == 1


def test_cancel_stale_revision(actors):
    application = complete(actors)
    assert cancel(actors, application, body={"expected_revision": 2}).status_code == 409
    assert_untouched(actors, application)


def test_key_scope_across_applications_and_users(actors):
    key = uuid4()
    first = complete(actors)
    second = complete(actors)
    assert submit(actors, first, key=key).status_code == 202
    assert submit(actors, second, key=key).status_code == 202
    body = second["form"]
    foreign = actors.second.post(
        "/api/applications", json=body, headers=headers(csrf(actors.second))
    )
    assert foreign.status_code == 201
    assert (
        submit(
            actors, foreign.json(), key=key, client=actors.second, h=headers(csrf(actors.second))
        ).status_code
        == 202
    )
    assert (
        submit(
            actors, first, key=key, client=actors.second, h=headers(csrf(actors.second))
        ).status_code
        == 404
    )


@pytest.mark.parametrize("race", ["same_key", "different_key", "patch", "cancel", "cancel_cancel"])
@pytest.mark.parametrize("iteration", range(3))
def test_concurrent_commands_from_independent_auth_sessions(actors, race, iteration):
    application = complete(actors)
    key = uuid4()
    barrier = Barrier(2)
    with TestClient(create_app(actors.settings), base_url=ORIGIN) as other:
        login(other, actors.account)  # Independent auth + CSRF row, not a copied cookie.
        h2 = headers(csrf(other))

        def command(client, h, second):
            barrier.wait(timeout=5)
            if race == "cancel_cancel" or (second and race in {"patch", "cancel"}):
                operation = "cancel" if race in {"cancel", "cancel_cancel"} else ""
                return client.request(
                    "POST" if operation else "PATCH",
                    path(application) + ("/" + operation if operation else ""),
                    headers=h,
                    json={"expected_revision": 1},
                )
            return submit(
                actors,
                application,
                key=uuid4() if second and race == "different_key" else key,
                client=client,
                h=h,
            )

        with ThreadPoolExecutor(2) as executor:
            futures = [
                executor.submit(command, actors.first, actors.h, False),
                executor.submit(command, other, h2, True),
            ]
            responses = [f.result(timeout=15) for f in futures]
    codes = sorted(r.status_code for r in responses)
    if race == "same_key":
        assert codes == [202, 202]
        assert responses[0].json() == responses[1].json()
    else:
        assert 409 in codes and sum(code < 300 for code in codes) == 1, codes
    app, events, snapshots, jobs, keys = records(actors, application)
    assert app.revision == 2
    assert [(e.sequence, e.revision) for e in events] == [(1, 1), (2, 2)]
    assert len(snapshots) == len(jobs) == len(keys) == (1 if app.status == "SUBMITTED" else 0)


@contextmanager
def fail_write(actors, application, table, operation="INSERT"):
    # Real PostgreSQL fault, scoped to this application's UUID; all unrelated rows survive.
    name = "b06_fault_" + uuid4().hex
    identity = application["id"]

    async def install(db):
        await db.execute(
            text(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'injected B06 write failure'; END $$""")
        )
        await db.execute(
            text(f"""CREATE TRIGGER {name} BEFORE {operation} ON {table}
        FOR EACH ROW WHEN (NEW.application_id = '{identity}'::uuid) EXECUTE FUNCTION {name}()""")
        )

    async def remove(db):
        await db.execute(text(f"DROP TRIGGER {name} ON {table}"))
        await db.execute(text(f"DROP FUNCTION {name}()"))

    in_database(actors.settings, install)
    try:
        yield
    finally:
        in_database(actors.settings, remove)


@pytest.mark.parametrize(
    "table",
    ["application_submissions", "pipeline_jobs", "application_events", "submit_idempotency"],
)
def test_submit_write_failure_rolls_back_everything(actors, table):
    application = complete(actors)
    key = uuid4()
    with fail_write(actors, application, table):
        with TestClient(
            create_app(actors.settings), base_url=ORIGIN, raise_server_exceptions=False
        ) as client:
            client.cookies.update(actors.first.cookies)
            response = submit(actors, application, key=key, client=client)
            assert response.status_code == 500
            assert "injected" not in response.text
    assert_untouched(actors, application)
    assert submit(actors, application, key=key).status_code == 202


@pytest.mark.parametrize(
    "table,operation", [("pipeline_jobs", "UPDATE"), ("application_events", "INSERT")]
)
def test_cancel_write_failure_preserves_submission_and_job(actors, table, operation):
    application = complete(actors)
    submitted = submit(actors, application).json()
    with fail_write(actors, application, table, operation):
        with TestClient(
            create_app(actors.settings), base_url=ORIGIN, raise_server_exceptions=False
        ) as client:
            client.cookies.update(actors.first.cookies)
            response = client.post(
                path(application) + "/cancel", headers=actors.h, json={"expected_revision": 2}
            )
            assert response.status_code == 500
    app, events, snapshots, jobs, keys = records(actors, application)
    assert (app.status, app.revision) == ("SUBMITTED", 2)
    assert len(events) == 2 and jobs[0].state == "pending"
    assert job_applicable(app, snapshots[0], jobs[0]) and len(keys) == 1
    assert cancel(actors, submitted).status_code == 200


def test_input_snapshot_survives_catalog_change_and_worker_status_revisions(actors):
    async def own_product(db):
        product = Product(code=uuid4().hex, name="Снимок", description="Тест", purposes=[])
        db.add(product)
        await db.flush()
        return str(product.id)

    product_id = in_database(actors.settings, own_product)
    application = complete(actors, product_id=product_id)
    submitted = submit(actors, application).json()
    original = records(actors, application)[2][0]

    async def advance(db):
        for revision, target in [(2, "CHECKING"), (3, "SCORING")]:
            await transition(
                db,
                UUID(application["id"]),
                target,
                expected_revision=revision,
                actor_kind="system",
                actor_user_id=None,
                request_id=uuid4(),
                public_message="Проверка заявки",
                audit_payload={},
            )
        product = await db.get(Product, UUID(product_id))
        product.name = "Новая версия"
        product.version += 1
        product.policy_snapshot = {"changed": True}

    in_database(actors.settings, advance)
    app, events, snapshots, jobs, _ = records(actors, application)
    assert app.revision == 4 and app.input_revision == 2
    assert snapshots[0].form_snapshot == original.form_snapshot == submitted["form"]
    assert snapshots[0].product_snapshot == original.product_snapshot
    assert job_applicable(app, snapshots[0], jobs[0])
    app.input_revision = 3
    assert not job_applicable(app, snapshots[0], jobs[0])
    assert [e.sequence for e in events] == [1, 2, 3, 4]
    assert cancel(actors, {**application, "revision": 4}).status_code == 200
    app, _, snapshots, jobs, _ = records(actors, application)
    assert not job_applicable(app, snapshots[0], jobs[0])


def test_cancel_revokes_claimed_job_and_preserves_lease_fencing(actors):
    application = complete(actors)
    submitted = submit(actors, application).json()

    async def claim(db):
        job = await db.scalar(
            select(PipelineJob)
            .where(PipelineJob.application_id == UUID(application["id"]))
            .with_for_update(skip_locked=True)
        )
        job.state = "running"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)

    in_database(actors.settings, claim)
    assert cancel(actors, submitted).status_code == 200
    app, _, snapshots, jobs, _ = records(actors, application)
    assert jobs[0].state == "cancelled" and jobs[0].lease_token is None
    assert jobs[0].lease_expires_at is None and jobs[0].attempts == 1
    assert not job_applicable(app, snapshots[0], jobs[0])


def test_database_guards_duplicate_jobs_keys_and_input_versions(actors):
    application = complete(actors)
    submit(actors, application)
    _, _, snapshots, jobs, keys = records(actors, application)

    async def violate(db, kind):
        if kind == "job":
            db.add(
                PipelineJob(
                    application_id=jobs[0].application_id,
                    submission_id=jobs[0].submission_id,
                    kind=jobs[0].kind,
                )
            )
        elif kind == "key":
            row = keys[0]
            db.add(
                SubmitIdempotency(
                    user_id=row.user_id,
                    application_id=row.application_id,
                    operation="submit",
                    key=row.key,
                    request_body=row.request_body,
                    response_body=row.response_body,
                    response_status=202,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
            )
        else:
            row = snapshots[0]
            db.add(
                ApplicationSubmission(
                    application_id=row.application_id,
                    input_revision=row.input_revision,
                    form_snapshot=row.form_snapshot,
                    product_snapshot=row.product_snapshot,
                    consent_to_checks=True,
                    submitted_at=row.submitted_at,
                )
            )
        await db.flush()

    for kind in ("job", "key", "input"):
        with pytest.raises(IntegrityError):
            in_database(actors.settings, lambda db, kind=kind: violate(db, kind))


def test_helpers_do_not_commit_and_multiple_events_allocate_sequence(actors):
    application = complete(actors)

    async def rollback(db):
        app = await lock_application(db, UUID(application["id"]))
        await append_event(
            db,
            app,
            actor_kind="system",
            actor_user_id=None,
            request_id=uuid4(),
            event_type="application_updated",
            previous_status="DRAFT",
            public_message="Тест",
            audit_payload={},
        )
        await append_event(
            db,
            app,
            actor_kind="system",
            actor_user_id=None,
            request_id=uuid4(),
            event_type="application_updated",
            previous_status="DRAFT",
            public_message="Тест",
            audit_payload={},
        )
        await transition(
            db,
            app.id,
            "SUBMITTED",
            expected_revision=1,
            actor_kind="client",
            actor_user_id=app.owner_id,
            request_id=uuid4(),
            public_message="Тест",
            audit_payload={},
        )
        await db.flush()
        assert list(
            await db.scalars(
                select(ApplicationEvent.sequence)
                .where(ApplicationEvent.application_id == app.id)
                .order_by(ApplicationEvent.sequence)
            )
        ) == [1, 2, 3, 4]
        raise RuntimeError("command failed")

    with pytest.raises(RuntimeError):
        in_database(actors.settings, rollback)
    assert_untouched(actors, application)


def test_transition_graph_and_initiators_match_document():
    document = (Path(__file__).resolve().parents[2] / "docs/application-states.md").read_text()
    graph = set(re.findall(r"^    (\w+) --> (\w+):?", document, flags=re.M))
    assert graph == {
        (source, target) for source, targets in TRANSITIONS.items() for target in targets
    }
    assert initiator_allowed("DRAFT", "SUBMITTED", "client")
    assert not initiator_allowed("SCORING", "APPROVED", "client")
    assert not initiator_allowed("MANUAL_REVIEW", "APPROVED", "system")
    assert initiator_allowed("MANUAL_REVIEW", "APPROVED", "underwriter")
    assert not initiator_allowed("SIGNED", "CANCELLED", "client")
    assert initiator_allowed("CHECKING", "CHECKING", "system")


def test_forbidden_initiator_cannot_mutate_valid_edge(actors):
    application = complete(actors)

    async def forbidden(db):
        await transition(
            db,
            UUID(application["id"]),
            "SUBMITTED",
            expected_revision=1,
            actor_kind="system",
            actor_user_id=None,
            request_id=uuid4(),
            public_message="Тест",
            audit_payload={},
        )

    with pytest.raises(HTTPException) as error:
        in_database(actors.settings, forbidden)
    assert error.value.status_code == 403
    assert_untouched(actors, application)


def test_cancel_preserves_decision_and_accepted_offer(actors):
    application = complete(actors)

    async def arrange(db):
        decision = Decision(
            application_id=UUID(application["id"]),
            input_revision=1,
            revision=2,
            outcome="APPROVED",
            source="automatic",
            public_reasons=[],
            public_explanation="Одобрено",
            required_documents=[],
            scoring_factors=[{"secret": "BANK-ONLY"}],
            analytical_explanation="BANK-ONLY",
        )
        db.add(decision)
        await db.flush()
        offer = Offer(
            application_id=decision.application_id,
            decision_id=decision.id,
            product_id=UUID(application["form"]["product_id"]),
            product_snapshot={},
            amount="100000.00",
            term_months=12,
            annual_rate_percent="26.0",
        )
        db.add(offer)
        await db.flush()
        app = await db.get(Application, decision.application_id)
        app.current_decision_id = decision.id
        app.accepted_offer_id = offer.id
        app.status = "OFFER_ACCEPTED"
        app.revision = 3
        return decision.id, offer.id

    decision_id, offer_id = in_database(actors.settings, arrange)
    response = cancel(actors, {**application, "revision": 3})
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    assert response.json()["decision"]["id"] == str(decision_id)
    assert response.json()["accepted_offer_id"] == str(offer_id)
    assert "BANK-ONLY" not in response.text

    async def retained(db):
        assert await db.get(Decision, decision_id) is not None
        assert await db.get(Offer, offer_id) is not None

    in_database(actors.settings, retained)


def test_skip_locked_allows_independent_claim_and_does_not_lock_application(actors):
    application = complete(actors)
    submit(actors, application)

    async def check(db):
        from app.database import create_session_factory

        query = (
            select(PipelineJob)
            .where(PipelineJob.application_id == UUID(application["id"]))
            .with_for_update(skip_locked=True)
        )
        first = await db.scalar(query)
        assert first is not None
        async with create_session_factory(db.bind)() as second:
            assert await second.scalar(query) is None
            assert (
                await second.scalar(
                    select(Application)
                    .where(Application.id == UUID(application["id"]))
                    .with_for_update(nowait=True)
                )
                is not None
            )
            # Claiming the job must not keep a transaction open for network work.
            await db.commit()
            assert (await second.scalar(query)).id == first.id
            await second.rollback()

    in_database(actors.settings, check)
