"""B11 accept-offer, sign + issuance, additional info and documents on PostgreSQL 16."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import anyio
import httpx
import pytest
from fastapi import Request
from sqlalchemy import select
from test_auth_postgres import csrf, headers, in_database
from test_decision_worker_postgres import company_key as company_key
from test_decision_worker_postgres import queued as queued
from test_decision_worker_postgres import ready as ready
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings
from test_submissions_postgres import cancel, complete, path, submit
from test_worker_postgres import rig as rig

from app import documents
from app.dependencies import database_session
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationSubmission,
    Decision,
    Document,
    PipelineJob,
)

PDF = b"%PDF-1.4\n% synthetic statement\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def storage(actors, tmp_path):
    """Every API instance of this test writes into its own temporary directory."""
    settings = actors.settings.model_copy(
        update={"documents_dir": tmp_path / "documents", "document_max_bytes": 1024}
    )
    for client in (actors.first, actors.second, actors.bank):
        client.app.state.settings = settings
    return settings


def card(actors, application, client=None):
    response = (client or actors.first).get(path(application))
    assert response.status_code == 200, response.text
    return response.json()


def upload(
    actors,
    application,
    *,
    revision=None,
    content=PDF,
    name="Выписка за год.pdf",
    media="application/pdf",
    document_type="bank_statement_12m",
    client=None,
    h=None,
):
    return (client or actors.first).post(
        path(application) + "/documents",
        files={"file": (name, content, media)},
        data={
            "document_type": document_type,
            "expected_revision": str(revision or card(actors, application)["revision"]),
        },
        headers=h or actors.h,
    )


def post(actors, application, action, body, *, client=None, h=None):
    return (client or actors.first).post(
        path(application) + "/" + action, json=body, headers=h or actors.h
    )


async def drain(rig):
    """Run every ready job (checks, then decision) with the scenario's mock answers."""
    async with rig.worker(rig.handler) as worker:
        while (claim := await worker.claim()) is not None:
            await worker.process(claim)


async def in_rig(rig, callback):
    async with rig.sessions.begin() as db:
        return await callback(db)


async def events(rig):
    async with rig.sessions() as db:
        return list(
            await db.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == UUID(rig.application["id"]))
                .order_by(ApplicationEvent.sequence)
            )
        )


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["approve", "counter_product"])
async def test_accept_sign_issue_and_guards(ready):
    rig = ready
    await drain(rig)
    actors, decided = rig.actors, card(rig.actors, rig.application)
    assert decided["status"] == rig.company["expected_outcome"]
    assert decided["allowed_actions"] == ["accept_offer", "cancel"]
    offer_id = decided["decision"]["offer"]["id"]
    revision = decided["revision"]
    bank = headers(csrf(actors.bank))
    second = headers(csrf(actors.second))
    accept = {"expected_revision": revision, "offer_id": offer_id}

    assert (
        post(actors, decided, "accept-offer", accept, client=actors.bank, h=bank).status_code == 403
    )
    assert (
        post(actors, decided, "accept-offer", accept, client=actors.second, h=second).status_code
        == 404
    )
    for body in (
        {**accept, "offer_id": str(uuid4())},
        {**accept, "expected_revision": revision - 1},
    ):
        assert post(actors, decided, "accept-offer", body).status_code == 409
    sign = {"expected_revision": revision, "offer_id": offer_id, "consent": True}
    assert post(actors, decided, "sign", sign).status_code == 409
    assert card(actors, decided)["revision"] == revision

    response = post(actors, decided, "accept-offer", accept)
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["status"] == "OFFER_ACCEPTED" and accepted["revision"] == revision + 1
    assert accepted["accepted_offer_id"] == offer_id
    assert accepted["allowed_actions"] == ["sign", "cancel"]
    assert accepted["decision"] == decided["decision"]
    assert (
        post(
            actors, decided, "accept-offer", {**accept, "expected_revision": revision + 1}
        ).status_code
        == 409
    )

    sign = {**sign, "expected_revision": accepted["revision"]}
    assert post(actors, decided, "sign", {**sign, "offer_id": str(uuid4())}).status_code == 409
    response = post(actors, decided, "sign", sign)
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["status"] == "ISSUED" and issued["revision"] == revision + 3
    assert issued["allowed_actions"] == [] and issued["accepted_offer_id"] == offer_id
    assert card(actors, decided) == issued
    assert post(actors, decided, "sign", sign).status_code == 409
    assert cancel(actors, issued).status_code == 409

    tail = (await events(rig))[-3:]
    assert [(e.previous_status, e.status, e.actor_kind, e.revision) for e in tail] == [
        (decided["status"], "OFFER_ACCEPTED", "client", revision + 1),
        ("OFFER_ACCEPTED", "SIGNED", "client", revision + 2),
        ("SIGNED", "ISSUED", "system", revision + 3),
    ]
    assert tail[0].audit_payload == {"offer_id": offer_id}
    assert tail[1].request_id == tail[2].request_id
    assert all(e.event_type == "status_changed" for e in tail)


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["approve"])
async def test_accept_rejects_offer_of_an_older_decision(ready):
    rig = ready
    await drain(rig)
    actors, decided = rig.actors, card(rig.actors, rig.application)
    old_offer = decided["decision"]["offer"]["id"]

    async def newer_decision(db):
        app = await db.get(Application, UUID(decided["id"]))
        old = await db.get(Decision, app.current_decision_id)
        row = Decision(
            application_id=app.id,
            input_revision=old.input_revision,
            revision=app.revision,
            outcome="APPROVED",
            source="underwriter",
            author_id=None,
            policy_version=None,
            public_reasons=[],
            public_explanation="Новое решение",
            required_documents=[],
            scoring_factors=[],
            analytical_explanation="",
        )
        db.add(row)
        await db.flush()
        app.current_decision_id = row.id

    await in_rig(rig, newer_decision)
    body = {"expected_revision": decided["revision"], "offer_id": old_offer}
    assert post(actors, decided, "accept-offer", body).status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["needs_info"])
async def test_needs_info_upload_and_revenue_fix_rechecks_to_approved(ready, storage):
    rig = ready
    await drain(rig)
    actors, asked = rig.actors, card(rig.actors, rig.application)
    assert asked["status"] == "NEEDS_INFO"
    assert {d["code"] for d in asked["decision"]["required_documents"]} == {
        "annual_revenue",
        "bank_statement_12m",
    }
    assert asked["allowed_actions"] == ["provide_info", "upload_document", "cancel"]

    response = upload(actors, asked, revision=asked["revision"])
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["document_type"] == "bank_statement_12m"
    assert document["media_type"] == "application/pdf" and document["size_bytes"] == len(PDF)
    assert document["filename"] == "Выписка за год.pdf"
    after_upload = card(actors, asked)
    assert after_upload["revision"] == asked["revision"] + 1
    assert after_upload["status"] == "NEEDS_INFO" and after_upload["decision"] == asked["decision"]

    info = {
        "expected_revision": after_upload["revision"],
        "comment": "Уточнили выручку",
        "document_ids": [document["id"]],
        "annual_revenue": "9400000.00",
    }
    stale = {**info, "expected_revision": asked["revision"]}
    assert post(actors, asked, "additional-info", stale).status_code == 409
    foreign = {**info, "document_ids": [str(uuid4())]}
    response = post(actors, asked, "additional-info", foreign)
    assert response.status_code == 422 and response.json()["field_errors"]
    assert card(actors, asked) == after_upload

    response = post(actors, asked, "additional-info", info)
    assert response.status_code == 202, response.text
    checking = response.json()
    assert checking["status"] == "CHECKING" and checking["revision"] == after_upload["revision"] + 1
    assert checking["form"]["annual_revenue"] == "9400000.00"
    assert checking["allowed_actions"] == ["cancel"]
    # The previous public decision stays until a new one replaces it.
    assert checking["decision"] == asked["decision"]
    assert (
        post(
            actors, asked, "additional-info", {**info, "expected_revision": checking["revision"]}
        ).status_code
        == 409
    )
    assert upload(actors, asked, revision=checking["revision"]).status_code == 409

    async def inputs(db):
        app = await db.get(Application, UUID(asked["id"]))
        rows = list(
            await db.scalars(
                select(ApplicationSubmission)
                .where(ApplicationSubmission.application_id == app.id)
                .order_by(ApplicationSubmission.input_revision)
            )
        )
        jobs = list(
            await db.scalars(select(PipelineJob).where(PipelineJob.application_id == app.id))
        )
        return app, rows, jobs

    app, submissions, jobs = await in_rig(rig, inputs)
    assert [s.input_revision for s in submissions] == [2, checking["revision"]]
    assert app.input_revision == checking["revision"]
    assert (
        submissions[0].document_types == []
        and submissions[0].form_snapshot["annual_revenue"] == "18000000.00"
    )
    assert submissions[1].document_types == ["bank_statement_12m"]
    assert submissions[1].form_snapshot["annual_revenue"] == "9400000.00"
    assert submissions[1].submitted_at == app.updated_at
    assert sorted(j.state for j in jobs) == ["completed", "pending"]
    tail = (await events(rig))[-1]
    assert (tail.previous_status, tail.status, tail.actor_kind) == (
        "NEEDS_INFO",
        "CHECKING",
        "client",
    )
    assert tail.audit_payload == {
        "comment": "Уточнили выручку",
        "document_ids": [document["id"]],
        "annual_revenue_changed": True,
    }

    await drain(rig)
    final = card(actors, asked)
    expected = rig.company["expected_after_info"]
    assert final["status"] == "APPROVED" == expected["outcome"], final
    assert final["decision"]["id"] != asked["decision"]["id"]
    terms = final["decision"]["offer"]["terms"]
    assert (terms["amount"], terms["term_months"]) == ("3000000.00", 24)


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["needs_info"])
async def test_needs_info_statement_without_revenue_fix_goes_to_manual_review(ready):
    rig = ready
    await drain(rig)
    actors, asked = rig.actors, card(rig.actors, rig.application)
    document = upload(actors, asked, revision=asked["revision"]).json()
    info = {
        "expected_revision": asked["revision"] + 1,
        "comment": "Приложили выписку",
        "document_ids": [document["id"]],
    }
    response = post(actors, asked, "additional-info", info)
    assert response.status_code == 202, response.text
    assert response.json()["form"]["annual_revenue"] == "18000000.00"
    await drain(rig)
    final = card(actors, asked)
    assert final["status"] == "MANUAL_REVIEW", final

    async def review(db):
        row = await db.get(Decision, UUID(final["decision"]["id"]))
        return row.risk_result["review_reason"]

    assert await in_rig(rig, review) == "revenue_mismatch_review"


def test_upload_validation_listing_download_and_access(actors, storage):
    application = complete(actors)
    second, bank = headers(csrf(actors.second)), headers(csrf(actors.bank))
    revision = application["revision"]
    cases = [
        ({"document_type": "DEMO_DOCUMENT"}, 422),
        ({"document_type": "annual_revenue"}, 422),
        ({"content": b"plain text", "name": "a.txt", "media": "text/plain"}, 415),
        ({"media": "image/png"}, 415),
        ({"content": b"%PDF" + b"0" * 1024}, 413),
        ({"content": b""}, 422),
    ]
    for changes, status in cases:
        response = upload(actors, application, revision=revision, **changes)
        assert response.status_code == status, (changes, response.text)
    assert upload(actors, application, revision=revision + 1).status_code == 409
    assert upload(actors, application, client=actors.bank, h=bank).status_code == 403
    assert upload(actors, application, client=actors.second, h=second).status_code == 404
    assert card(actors, application)["revision"] == revision

    response = upload(
        actors,
        application,
        revision=revision,
        content=PNG,
        name="C:\\fakepath\\..\\скан.png",
        media="image/png",
        document_type="lease_agreement",
    )
    assert response.status_code == 201, response.text
    first = response.json()
    assert first["filename"] == "скан.png" and first["media_type"] == "image/png"
    assert first["application_id"] == application["id"]
    assert first["uploaded_by"] == actors.first.get("/api/auth/me").json()["id"]
    response = upload(actors, application, revision=revision + 1, media="application/octet-stream")
    assert response.status_code == 201, response.text
    second_doc = response.json()

    listing = actors.first.get(path(application) + "/documents")
    assert listing.status_code == 200
    assert [d["id"] for d in listing.json()["items"]] == [first["id"], second_doc["id"]]
    assert actors.bank.get(path(application) + "/documents").json() == listing.json()
    assert actors.second.get(path(application) + "/documents").status_code == 404
    assert "storage" not in listing.text and str(storage.documents_dir) not in listing.text

    url = path(application) + f"/documents/{first['id']}/download"
    for client in (actors.first, actors.bank):
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; filename=")
        assert "filename*=UTF-8''%D1%81%D0%BA%D0%B0%D0%BD.png" in disposition
    assert actors.second.get(url).status_code == 404
    assert actors.first.get(path(application) + f"/documents/{uuid4()}/download").status_code == 404

    async def stored(db):
        row = await db.get(Document, UUID(first["id"]))
        app = await db.get(Application, UUID(application["id"]))
        events = list(
            await db.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == app.id)
                .order_by(ApplicationEvent.sequence)
            )
        )
        return row, app, events

    row, app, history = in_database(actors.settings, stored)
    assert (
        row.sha256 == hashlib.sha256(PNG).hexdigest() and row.application_revision == revision + 1
    )
    assert app.revision == revision + 2 and app.status == "DRAFT"
    assert [e.event_type for e in history[-2:]] == ["document_uploaded"] * 2
    assert history[-2].audit_payload == {"document_type": "lease_agreement", "size_bytes": len(PNG)}
    blob = storage.documents_dir / row.storage_key[:2] / row.storage_key
    assert blob.read_bytes() == PNG
    blob.write_bytes(b"\x89PNG tampered")
    assert actors.first.get(url).status_code == 404
    blob.write_bytes(bytes([PNG[0] ^ 1]) + PNG[1:])  # Same size, wrong sha256.
    assert actors.first.get(url).status_code == 404
    blob.unlink()
    assert actors.first.get(url).status_code == 404

    # Documents uploaded in DRAFT become part of the first input version.
    current = card(actors, application)
    assert submit(actors, current).status_code == 202

    async def frozen(db):
        return await db.scalar(
            select(ApplicationSubmission.document_types).where(
                ApplicationSubmission.application_id == UUID(application["id"])
            )
        )

    assert in_database(actors.settings, frozen) == ["bank_statement_12m", "lease_agreement"]
    assert upload(actors, application).status_code == 409


@pytest.mark.parametrize("operation", ["upload", "download"])
def test_document_io_does_not_block_other_request(actors, monkeypatch, operation):
    application = complete(actors)
    url = path(application) + "/documents"
    if operation == "download":
        document = upload(actors, application).json()
        url += f"/{document['id']}/download"
    original = getattr(documents, "write_file" if operation == "upload" else "read_file")
    entered, release = Event(), Event()

    def delayed(*args):
        entered.set()
        assert release.wait(15), "file barrier was not released"
        return original(*args)

    monkeypatch.setattr(documents, original.__name__, delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = (
            pool.submit(upload, actors, application, revision=application["revision"])
            if operation == "upload"
            else pool.submit(actors.first.get, url)
        )
        try:
            assert entered.wait(5), "file operation did not start"
            # The same TestClient uses one lifespan/portal and thus the same API loop.
            # An external future deadline lets finally release even a blocked loop.
            neighbor = pool.submit(actors.first.get, "/api/auth/me")
            response = neighbor.result(timeout=5)
            assert response.status_code == 200
            assert not pending.done() and not release.is_set()
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == (201 if operation == "upload" else 200)
    if operation == "download":
        assert response.content == PDF
    else:
        url += f"/{response.json()['id']}/download"
        assert actors.first.get(url).content == PDF


def test_file_write_failure_rolls_back_metadata_revision_and_event(actors, monkeypatch):
    application = complete(actors)

    async def snapshot(db):
        app_id = UUID(application["id"])
        app = await db.get(Application, app_id)
        docs = list(await db.scalars(select(Document.id).where(Document.application_id == app_id)))
        events = list(
            await db.scalars(
                select(ApplicationEvent.id)
                .where(ApplicationEvent.application_id == app_id)
                .order_by(ApplicationEvent.sequence)
            )
        )
        return app.revision, app.updated_at, docs, events

    before = in_database(actors.settings, snapshot)

    def fail(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(documents, "write_file", fail)
    # TestClient normally re-raises server exceptions after sending the 500 response.
    monkeypatch.setattr(actors.first._transport, "raise_server_exceptions", False)
    response = upload(actors, application, revision=application["revision"])
    assert response.status_code == 500
    assert in_database(actors.settings, snapshot) == before


@pytest.mark.parametrize("operation", ["upload", "download"])
def test_cancelled_document_request_waits_for_file_worker(actors, monkeypatch, operation):
    application = complete(actors)
    url = path(application) + "/documents"
    if operation == "download":
        document = upload(actors, application).json()
        url += f"/{document['id']}/download"
    original = getattr(documents, "write_file" if operation == "upload" else "read_file")
    entered, release, closed, responded = Event(), Event(), Event(), Event()
    app = actors.first.app

    def delayed(*args):
        entered.set()
        assert release.wait(15), "file barrier was not released"
        assert not closed.is_set()
        return original(*args)

    async def tracked_session(request: Request):
        try:
            async with request.app.state.session_factory() as session:
                yield session
        finally:
            closed.set()

    async def request(*, task_status):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=str(actors.first.base_url),
            cookies=actors.first.cookies,
        ) as client:
            with anyio.CancelScope() as scope:
                task_status.started(scope)
                if operation == "upload":
                    await client.post(
                        url,
                        files={"file": ("statement.pdf", PDF, "application/pdf")},
                        data={
                            "document_type": "bank_statement_12m",
                            "expected_revision": str(application["revision"]),
                        },
                        headers=actors.h,
                    )
                else:
                    await client.get(url)
                responded.set()

    monkeypatch.setattr(documents, original.__name__, delayed)
    app.dependency_overrides[database_session] = tracked_session
    pending, scope = actors.first.portal.start_task(request)
    try:
        assert entered.wait(5)
        actors.first.portal.call(scope.cancel)
        # A round trip on the API loop also gives cancellation a chance to run.
        assert actors.first.get("/health/live").status_code == 200
        assert not closed.is_set() and not pending.done() and not responded.is_set()
    finally:
        release.set()
        try:
            pending.result(timeout=5)
        finally:
            app.dependency_overrides.pop(database_session)
    assert closed.is_set() and not responded.is_set()


def test_actions_require_matching_status(actors):
    application = complete(actors)
    revision = application["revision"]
    for action, body in (
        ("accept-offer", {"expected_revision": revision, "offer_id": str(uuid4())}),
        ("sign", {"expected_revision": revision, "offer_id": str(uuid4()), "consent": True}),
        ("additional-info", {"expected_revision": revision, "comment": "Нет запроса"}),
    ):
        assert post(actors, application, action, body).status_code == 409
    assert card(actors, application) == application
