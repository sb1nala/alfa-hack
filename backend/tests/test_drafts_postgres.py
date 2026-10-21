"""B05 wire behavior on an isolated real PostgreSQL 16, including competing sessions."""

import asyncio
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from test_auth_postgres import ORIGIN, SECRET, csrf, headers, in_database, login

from app.database import create_engine, create_session_factory
from app.main import create_app
from app.models import Application, ApplicationEvent, Company, Decision, Offer, Product, User
from app.security import password_hasher
from app.seed import ImportError, import_seed

ROOT = Path(__file__).resolve().parents[2]
COMPANIES = ROOT / "seed/companies.json"
PRODUCTS = ROOT / "seed/products.json"
TEXTS = ROOT / "docs/product/ui-texts.ru.json"
COMPANY = json.loads(COMPANIES.read_text())["companies"][0]["company"]


@pytest.fixture(scope="session")
def draft_settings(database):
    settings = database.model_copy(
        update={
            "auth_jwt_secret": SecretStr(SECRET),
            "auth_allowed_origin": ORIGIN,
            "auth_cookie_secure": True,
            "egrul_timeout": 0.15,
        }
    )

    async def seed(db):
        await import_seed(
            create_session_factory(db.bind),
            companies=COMPANIES,
            products=PRODUCTS,
            product_texts=TEXTS,
        )

    in_database(settings, seed)
    return settings


@pytest.fixture(scope="session")
def demo_hash():
    return password_hasher.hash("b05-password")


@pytest.fixture
def actors(draft_settings, demo_hash):
    accounts = [
        {"login": "b05-" + uuid4().hex, "password": "b05-password", "role": role}
        for role in ("client", "client", "underwriter")
    ]

    async def insert(db):
        for account in accounts:
            db.add(
                User(
                    login=account["login"],
                    password_hash=demo_hash,
                    display_name="Тест B05",
                    role=account["role"],
                )
            )

    in_database(draft_settings, insert)
    with ExitStack() as stack:
        clients = [
            stack.enter_context(TestClient(create_app(draft_settings), base_url=ORIGIN))
            for _ in accounts
        ]
        for client, account in zip(clients, accounts, strict=True):
            login(client, account)
        yield SimpleNamespace(
            settings=draft_settings,
            first=clients[0],
            second=clients[1],
            bank=clients[2],
            account=accounts[0],
            h=headers(csrf(clients[0])),
        )


def create(actors, body=None):
    response = actors.first.post("/api/applications", json=body or {}, headers=actors.h)
    assert response.status_code == 201, response.text
    result = response.json()
    assert response.headers["Location"] == "/api/applications/" + result["id"]
    return result


def patch(actors, application, body):
    return actors.first.patch("/api/applications/" + application["id"], json=body, headers=actors.h)


def test_empty_draft_create_get_patch_restart_and_audit(actors):
    application = create(actors)
    path = "/api/applications/" + application["id"]
    assert application["status"] == "DRAFT" and application["revision"] == 1
    assert application["owner_id"] == actors.first.get("/api/auth/me").json()["id"]
    assert application["allowed_actions"] == ["edit", "submit", "cancel", "upload_document"]
    assert all(value is None for value in application["form"].values())
    assert application["decision"] is None and application["accepted_offer_id"] is None
    assert application["created_at"].endswith("Z")
    assert actors.first.get(path).json() == application
    response = patch(
        actors,
        application,
        {
            "expected_revision": 1,
            "amount": "999999999999999999999999999.99",
            "annual_revenue": "0.00",
            "purpose": "Закупка",
            "term_months": 12,
        },
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["revision"] == 2
    assert edited["form"]["amount"] == "999999999999999999999999999.99"
    assert edited["form"]["annual_revenue"] == "0.00"
    assert edited["updated_at"] > application["updated_at"]
    with TestClient(create_app(actors.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(actors.first.cookies)
        assert restarted.get(path).json() == edited

    async def check(db):
        rows = (
            await db.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == UUID(application["id"]))
                .order_by(ApplicationEvent.sequence)
            )
        ).all()
        assert [(r.sequence, r.revision) for r in rows] == [(1, 1), (2, 2)]
        assert rows[0].previous_status is None
        assert str(rows[1].request_id) == response.headers["X-Request-ID"]

    in_database(actors.settings, check)


def test_product_catalog_and_reference_snapshots(actors):
    response = actors.first.get("/api/products")
    assert response.status_code == 200
    products = response.json()["items"]
    assert {p["code"] for p in products} >= {
        "express",
        "investment",
        "overdraft",
        "working_capital_line",
    }
    assert all(set(p) == {"id", "code", "name", "description", "purposes"} for p in products)
    product = next(p for p in products if p["code"] == "express")
    assert product["name"] == json.loads(TEXTS.read_text())["products"]["express"]["name"]
    assert "purchase_goods" in product["purposes"]
    assert actors.bank.get("/api/products").status_code == 200
    application = create(actors, {"company": COMPANY, "product_id": product["id"]})

    async def check(db):
        row = await db.get(Application, UUID(application["id"]))
        assert row.company_id == await db.scalar(
            select(Company.id).where(Company.inn == COMPANY["inn"])
        )
        risk = json.loads(PRODUCTS.read_text())
        policy = row.product_snapshot["policy"]
        assert policy == {"policy_version": risk["_meta"]["policy_version"], **risk["products"][0]}
        assert row.product_snapshot["id"] == product["id"]

    in_database(actors.settings, check)

    edited = patch(actors, application, {"expected_revision": 1, "purpose": "Новая цель"}).json()
    assert edited["form"]["company"] == application["form"]["company"]
    assert edited["form"]["product_id"] == product["id"]
    response = patch(
        actors, application, {"expected_revision": 2, "company": None, "product_id": None}
    )
    assert response.status_code == 200
    assert response.json()["form"]["company"] is None
    assert response.json()["form"]["product_id"] is None

    async def cleared(db):
        row = await db.get(Application, UUID(application["id"]))
        assert row.company_id is None and row.company_snapshot is None
        assert row.product_id is None and row.product_snapshot is None

    in_database(actors.settings, cleared)


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "1.01"),
        ("annual_revenue", "0.00"),
        ("term_months", 1),
        ("purpose", "Цель"),
    ],
)
def test_patch_omitted_and_null_fields(actors, field, value):
    application = create(actors, {field: value})
    edited = patch(actors, application, {"expected_revision": 1}).json()
    assert edited["form"][field] == value and edited["revision"] == 2
    cleared = patch(actors, application, {"expected_revision": 2, field: None})
    assert cleared.status_code == 200
    assert cleared.json()["form"][field] is None and cleared.json()["revision"] == 3


def test_roles_ownership_csrf_and_server_owned_fields(actors):
    application = create(actors)
    path = "/api/applications/" + application["id"]
    for client, expected in ((actors.second, 404), (actors.bank, 403)):
        assert client.get(path).status_code == expected
        assert (
            client.patch(
                path, json={"expected_revision": 1}, headers=headers(csrf(client))
            ).status_code
            == expected
        )
    assert actors.bank.get("/api/applications").status_code == 403
    assert (
        actors.bank.post(
            "/api/applications", json={}, headers=headers(csrf(actors.bank))
        ).status_code
        == 403
    )
    assert actors.second.get("/api/applications").json()["total"] == 0
    for h in (
        {},
        {"Origin": ORIGIN},
        headers("invalid"),
        headers(actors.h["X-CSRF-Token"], "https://evil.test"),
    ):
        assert actors.first.post("/api/applications", json={}, headers=h).status_code == 403
        assert actors.first.patch(path, json={"expected_revision": 1}, headers=h).status_code == 403
    for field, value in (
        ("owner_id", application["owner_id"]),
        ("status", "APPROVED"),
        ("revision", 99),
    ):
        assert (
            actors.first.post(
                "/api/applications", json={field: value}, headers=actors.h
            ).status_code
            == 422
        )
    assert actors.first.get(path).json() == application


def test_conflicts_and_bad_reference_are_atomic(actors):
    application = create(actors, {"purpose": "Прежняя цель"})
    for revision in (2, 99):
        assert (
            patch(
                actors, application, {"expected_revision": revision, "purpose": "Не сохранять"}
            ).status_code
            == 409
        )
    assert (
        patch(
            actors,
            application,
            {"expected_revision": 1, "product_id": str(uuid4()), "company": COMPANY},
        ).status_code
        == 422
    )
    assert (
        patch(actors, application, {"expected_revision": 1, "term_months": 2**40}).status_code
        == 422
    )
    assert actors.first.get("/api/applications/" + application["id"]).json() == application

    async def change_status(db):
        await db.execute(
            update(Application)
            .where(Application.id == UUID(application["id"]))
            .values(status="SUBMITTED")
        )

    in_database(actors.settings, change_status)
    assert patch(actors, application, {"expected_revision": 1}).status_code == 409
    assert actors.first.get("/api/applications/" + application["id"]).json()["allowed_actions"] == [
        "cancel"
    ]


def test_archived_products_are_hidden_and_rejected(actors):
    async def archive(db):
        product = Product(
            code="archived-" + uuid4().hex,
            name="Архив",
            description="Архив",
            purposes=[],
            active=False,
        )
        db.add(product)
        await db.flush()
        return str(product.id)

    identity = in_database(actors.settings, archive)
    assert identity not in {p["id"] for p in actors.first.get("/api/products").json()["items"]}
    response = actors.first.post(
        "/api/applications", json={"product_id": identity}, headers=actors.h
    )
    assert response.status_code == 422
    assert response.json()["field_errors"][0]["field"] == "body.product_id"
    application = create(actors)
    assert (
        patch(actors, application, {"expected_revision": 1, "product_id": identity}).status_code
        == 422
    )
    assert actors.first.get("/api/applications/" + application["id"]).json()["revision"] == 1


def test_list_filter_pagination_total_and_tie_breaker(actors):
    applications = [create(actors) for _ in range(4)]
    stamp = datetime(2026, 10, 1, tzinfo=UTC)

    async def arrange(db):
        await db.execute(
            update(Application)
            .where(Application.owner_id == UUID(applications[0]["owner_id"]))
            .values(updated_at=stamp)
        )
        await db.execute(
            update(Application)
            .where(Application.id == UUID(applications[0]["id"]))
            .values(status="CANCELLED")
        )

    in_database(actors.settings, arrange)
    expected = sorted((a["id"] for a in applications), reverse=True)
    for offset in (0, 1, 3, 4, 100):
        result = actors.first.get("/api/applications", params={"offset": offset, "limit": 2}).json()
        assert result["total"] == 4 and result["offset"] == offset and result["limit"] == 2
        assert [a["id"] for a in result["items"]] == expected[offset : offset + 2]
    filtered = actors.first.get("/api/applications", params={"status": "DRAFT"}).json()
    assert filtered["total"] == 3
    assert all(a["status"] == "DRAFT" for a in filtered["items"])
    last = applications[-1]
    edited = patch(actors, last, {"expected_revision": 1}).json()
    assert actors.first.get("/api/applications").json()["items"][0]["id"] == edited["id"]


def test_concurrent_patch_same_revision_only_one_wins(actors):
    application = create(actors)
    with TestClient(create_app(actors.settings), base_url=ORIGIN) as other_session:
        login(other_session, actors.account)
        h2 = headers(csrf(other_session))
        barrier = Barrier(2)

        def edit(client, h, purpose):
            barrier.wait(timeout=5)
            return client.patch(
                "/api/applications/" + application["id"],
                json={"expected_revision": 1, "purpose": purpose},
                headers=h,
            )

        with ThreadPoolExecutor(2) as executor:
            futures = [
                executor.submit(edit, actors.first, actors.h, "Первое"),
                executor.submit(edit, other_session, h2, "Второе"),
            ]
            responses = [f.result(timeout=10) for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 409]
    saved = actors.first.get("/api/applications/" + application["id"]).json()
    assert saved["revision"] == 2
    assert saved == next(r.json() for r in responses if r.status_code == 200)

    async def check(db):
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ApplicationEvent)
                .where(ApplicationEvent.application_id == UUID(application["id"]))
            )
            == 2
        )

    in_database(actors.settings, check)


@pytest.mark.parametrize(
    "outcome", ["APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "MANUAL_REVIEW", "REJECTED"]
)
def test_card_returns_public_decision_without_internal_bank_data(actors, outcome):
    application = create(actors)

    async def decide(db):
        from app.api.examples import DECISIONS

        public = next(v for v in DECISIONS.values() if v["outcome"] == outcome)
        decision = Decision(
            application_id=UUID(application["id"]),
            input_revision=1,
            revision=2,
            outcome=outcome,
            source="automatic",
            public_reasons=public["reasons"],
            public_explanation="Публичное объяснение",
            required_documents=public["required_documents"],
            scoring_factors=[{"secret": "BANK-INTERNAL"}],
            analytical_explanation="BANK-INTERNAL",
        )
        db.add(decision)
        await db.flush()
        if outcome in {"APPROVED", "COUNTER_OFFER"}:
            product = await db.scalar(select(Product).where(Product.code == "express"))
            db.add(
                Offer(
                    application_id=UUID(application["id"]),
                    decision_id=decision.id,
                    product_id=product.id,
                    product_snapshot={},
                    amount="100000.00",
                    term_months=12,
                    annual_rate_percent="26.0",
                )
            )
        await db.execute(
            update(Application)
            .where(Application.id == UUID(application["id"]))
            .values(current_decision_id=decision.id, status=outcome, revision=2)
        )

    in_database(actors.settings, decide)
    response = actors.first.get("/api/applications/" + application["id"])
    assert response.status_code == 200, response.text
    assert response.json()["decision"]["outcome"] == outcome
    for secret in (
        "BANK-INTERNAL",
        "scoring_factors",
        "analytical_explanation",
        "policy_snapshot",
        "password_hash",
    ):
        assert secret not in response.text


def test_import_repeat_preserves_ids_hashes_and_risk_values(draft_settings):
    async def check(db):
        before_users = list(
            (await db.execute(select(User.id, User.password_hash).order_by(User.id))).all()
        )
        before_products = list(
            (
                await db.execute(
                    select(Product.id, Product.version, Product.policy_snapshot).order_by(
                        Product.id
                    )
                )
            ).all()
        )
        before_companies = list(
            (await db.execute(select(Company.id, Company.updated_at).order_by(Company.id))).all()
        )
        report = await import_seed(
            create_session_factory(db.bind),
            accounts=ROOT / "seed/accounts.json",
            companies=COMPANIES,
            products=PRODUCTS,
            product_texts=TEXTS,
        )
        assert report["Companies"] == (0, 9) and report["Products"] == (0, 4)
        assert before_products == list(
            (
                await db.execute(
                    select(Product.id, Product.version, Product.policy_snapshot).order_by(
                        Product.id
                    )
                )
            ).all()
        )
        assert before_companies == list(
            (await db.execute(select(Company.id, Company.updated_at).order_by(Company.id))).all()
        )
        for identity, password_hash in before_users:
            assert (await db.get(User, identity)).password_hash == password_hash

    in_database(draft_settings, check)


def test_concurrent_imports_preserve_unique_identities(draft_settings):
    def import_again(_):
        async def seed(db):
            return await import_seed(
                create_session_factory(db.bind),
                companies=COMPANIES,
                products=PRODUCTS,
                product_texts=TEXTS,
            )

        return in_database(draft_settings, seed)

    with ThreadPoolExecutor(2) as executor:
        reports = list(executor.map(import_again, range(2)))
    assert all(report == {"Companies": (0, 9), "Products": (0, 4)} for report in reports)


def test_product_reimport_versions_catalog_but_preserves_draft_snapshots(actors, tmp_path):
    product = next(
        p for p in actors.first.get("/api/products").json()["items"] if p["code"] == "express"
    )
    application = create(actors, {"product_id": product["id"], "company": COMPANY})
    changed_source = json.loads(PRODUCTS.read_text())
    changed_source["products"][0]["base_rate_percent"] = "26.001"
    path = tmp_path / "products.json"
    path.write_text(json.dumps(changed_source))

    async def update_catalog(db):
        before = await db.get(Application, UUID(application["id"]))
        old_snapshot = before.product_snapshot
        factory = create_session_factory(db.bind)
        report = await import_seed(factory, products=path, product_texts=TEXTS)
        assert report["Products"] == (1, 3)
        current = await db.get(Product, UUID(product["id"]))
        assert current.version == old_snapshot["version"] + 1
        assert current.policy_snapshot["base_rate_percent"] == "26.001"
        await db.refresh(before)
        assert before.product_snapshot == old_snapshot
        assert (await import_seed(factory, products=path, product_texts=TEXTS))["Products"] == (
            0,
            4,
        )
        # Restore the original synthetic catalog for the other tests.
        await import_seed(factory, products=PRODUCTS, product_texts=TEXTS)

    in_database(actors.settings, update_catalog)
    assert actors.first.get("/api/applications/" + application["id"]).json() == application


def test_import_failure_rolls_back_all_sources(draft_settings, tmp_path):
    company = {**COMPANY, "inn": "0000000011", "name": "Откатить"}
    companies = tmp_path / "companies.json"
    companies.write_text(json.dumps({"companies": [{"company": company}]}))
    accounts = tmp_path / "accounts.json"
    account_login = "rollback-" + uuid4().hex
    accounts.write_text(
        json.dumps(
            {
                "clients": [
                    {"login": account_login, "password": "demo", "display_name": "Откатить"}
                ],
                "underwriters": [],
            }
        )
    )

    async def check(db):
        # Fail after writes to verify rollback of all sources, beyond input parsing.
        bad = json.loads(PRODUCTS.read_text())
        bad["products"][0]["code"] = "bad\u0000code"
        texts = json.loads(TEXTS.read_text())
        texts["products"]["bad\u0000code"] = texts["products"]["express"]
        bad["purposes"] = {}
        product_path, text_path = tmp_path / "products.json", tmp_path / "texts.json"
        product_path.write_text(json.dumps(bad))
        text_path.write_text(json.dumps(texts))
        with pytest.raises(DBAPIError):
            await import_seed(
                create_session_factory(db.bind),
                accounts=accounts,
                companies=companies,
                products=product_path,
                product_texts=text_path,
            )
        assert await db.scalar(select(User.id).where(User.login == account_login)) is None
        assert await db.scalar(select(Company.id).where(Company.inn == company["inn"])) is None
        companies.write_text(
            json.dumps({"companies": [{"company": COMPANY}, {"company": COMPANY}]})
        )
        with pytest.raises(ImportError, match="Duplicate company"):
            await import_seed(create_session_factory(db.bind), companies=companies)

    in_database(draft_settings, check)


def registry_client(handler):
    return httpx.AsyncClient(base_url="http://mocks", transport=httpx.MockTransport(handler))


def test_lookup_uses_real_mock_and_links_result(actors, monkeypatch):
    spec = importlib.util.spec_from_file_location("b05_local_mocks", ROOT / "mocks/app/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = httpx.AsyncClient(
        base_url="http://mocks",
        transport=httpx.ASGITransport(app=module.create_app(seed_path=COMPANIES, latency_ms=0)),
    )
    monkeypatch.setattr(actors.first.app.state, "registry", registry)
    found = actors.first.get("/api/companies/lookup", params={"inn": COMPANY["inn"]})
    assert found.status_code == 200
    assert found.json()["company"] == COMPANY
    assert "director" not in found.text and "status" not in found.text
    application = create(actors, {"company": found.json()["company"]})

    async def check(db):
        assert (await db.get(Application, UUID(application["id"]))).company_id is not None

    in_database(actors.settings, check)
    missing = actors.first.get("/api/companies/lookup", params={"inn": "0000000000"})
    assert missing.status_code == 200 and missing.json() == {"company": None}


@pytest.mark.parametrize(
    "failure",
    ["timeout", "slow", "500", "503", "invalid-json", "bad-fields", "wrong-inn", "redirect"],
)
def test_lookup_failure_is_not_absence_and_has_bounded_duration(actors, monkeypatch, failure):
    async def handler(request):
        assert request.url.path == "/egrul/" + COMPANY["inn"]
        assert request.extensions["timeout"]["read"] == actors.settings.egrul_timeout
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream diagnostic")
        if failure == "slow":
            await asyncio.sleep(5)
        if failure in {"500", "503", "redirect"}:
            return httpx.Response(302 if failure == "redirect" else int(failure))
        if failure == "invalid-json":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(
            200, json={} if failure == "bad-fields" else {**COMPANY, "inn": "0000000000"}
        )

    monkeypatch.setattr(actors.first.app.state, "registry", registry_client(handler))
    started = time.monotonic()
    response = actors.first.get("/api/companies/lookup", params={"inn": COMPANY["inn"]})
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "company" not in response.json() and "private upstream" not in response.text
    assert time.monotonic() - started < 2
    assert actors.first.get("/api/applications").status_code == 200


def test_concurrent_lookup_has_one_identity_and_no_open_transaction_during_http(
    actors, monkeypatch
):
    company = {**COMPANY, "inn": "0000000022"}
    barrier = Barrier(2)

    def handler(request):
        # The application auth transaction must have ended before reaching the source.
        barrier.wait(timeout=5)

        async def active():
            # Observers must not create idle transactions seen by each other.
            engine = create_engine(actors.settings)
            try:
                async with engine.connect() as connection:
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    return await connection.scalar(
                        select(func.count())
                        .select_from(text("pg_stat_activity"))
                        .where(
                            text(
                                "datname = current_database() AND pid <> pg_backend_pid() "
                                "AND state = 'idle in transaction'"
                            )
                        )
                    )
            finally:
                await engine.dispose()

        assert asyncio.run(active()) == 0
        # Keep both requests in HTTP until both observations finish; otherwise
        # one request's valid post-HTTP UPSERT can race the other's observation.
        barrier.wait(timeout=5)
        return httpx.Response(200, json={**company, "director": "Internal", "status": "ACTIVE"})

    # MockTransport executes synchronous handlers on the caller loop; keep DB inspection in
    # that loop's independent helper thread so asyncio.run has no nested loop.
    async def async_handler(request):
        return await asyncio.to_thread(handler, request)

    for client in (actors.first, actors.second):
        # This test performs two PostgreSQL observations inside the HTTP barrier.
        # Give those observations their own bounded budget under Docker/CI load;
        # the separate timeout test above still verifies the 150ms HTTP deadline.
        monkeypatch.setattr(
            client.app.state, "settings", actors.settings.model_copy(update={"egrul_timeout": 5})
        )
        monkeypatch.setattr(client.app.state, "registry", registry_client(async_handler))
    with ThreadPoolExecutor(2) as executor:
        results = list(
            executor.map(
                lambda c: c.get("/api/companies/lookup", params={"inn": company["inn"]}),
                (actors.first, actors.second),
            )
        )
    assert [r.status_code for r in results] == [200, 200]

    async def check(db):
        assert (
            await db.scalar(
                select(func.count()).select_from(Company).where(Company.inn == company["inn"])
            )
            == 1
        )

    in_database(actors.settings, check)
