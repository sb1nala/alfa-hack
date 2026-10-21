"""Opt-in real PostgreSQL tests; only the randomly named DB created here is destroyed."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import (
    Application,
    ApplicationEvent,
    Base,
    Company,
    Decision,
    Document,
    Offer,
    Product,
    User,
)

pytestmark = pytest.mark.anyio
BACKEND = Path(__file__).resolve().parents[1]


def migrate(settings, *args):
    env = {
        **os.environ,
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_DB": settings.postgres_db,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.postgres_password.get_secret_value() not in result.stdout + result.stderr


@pytest.fixture(scope="module")
def database(request):
    if not request.config.getoption("--run-postgres"):
        pytest.skip("use --run-postgres with PostgreSQL 16 credentials (CREATE DATABASE required)")
    admin = Settings()
    name = "b03_test_" + uuid4().hex
    settings = admin.model_copy(update={"postgres_db": name})

    async def administer(statement):
        engine = create_engine(admin)
        try:
            async with engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                assert (await connection.scalar(text("SHOW server_version_num"))).startswith("16")
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    async def tables():
        engine = create_engine(settings)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        finally:
            await engine.dispose()

    asyncio.run(administer(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    try:
        migrate(settings, "upgrade", "head")
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        assert set(asyncio.run(tables())) == set(Base.metadata.tables) | {"alembic_version"}
        migrate(settings, "downgrade", "base")
        assert set(asyncio.run(tables())) == {"alembic_version"}
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        yield settings
    finally:
        asyncio.run(administer(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def session(database):
    engine = create_engine(database)
    try:
        async with create_session_factory(engine)() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


def decision(application_id, **changes):
    return Decision(
        **{
            "application_id": application_id,
            "input_revision": 1,
            "revision": 2,
            "outcome": "APPROVED",
            "source": "automatic",
            "public_reasons": [],
            "public_explanation": "Синтетическое решение",
            "required_documents": [],
            "scoring_factors": [],
            "analytical_explanation": "Тест",
            **changes,
        }
    )


def event(application_id, **changes):
    return ApplicationEvent(
        **{
            "application_id": application_id,
            "sequence": 1,
            "revision": 1,
            "event_type": "application_updated",
            "status": "DRAFT",
            "actor_kind": "system",
            "request_id": uuid4(),
            "public_message": "Создан черновик",
            "audit_payload": {},
            **changes,
        }
    )


@pytest.fixture
async def graph(session):
    owner = User(login=uuid4().hex, password_hash="test-hash", display_name="Тест", role="client")
    company = Company(inn="1234567890", name="Тест", legal_form="OOO")
    product = Product(code=uuid4().hex, name="Тест", description="Тест", purposes=["Тест"])
    session.add_all([owner, company, product])
    await session.flush()
    app = Application(owner_id=owner.id)
    other = Application(owner_id=owner.id)
    session.add_all([app, other])
    await session.flush()
    result = decision(app.id)
    audit = event(app.id)
    document = Document(
        application_id=app.id,
        uploaded_by=owner.id,
        application_revision=1,
        document_type="DEMO",
        filename="demo.txt",
        media_type="text/plain",
        size_bytes=0,
        storage_key=uuid4().hex,
        sha256="0" * 64,
    )
    session.add_all([result, audit, document])
    await session.flush()
    offer = Offer(
        application_id=app.id,
        decision_id=result.id,
        product_id=product.id,
        product_snapshot={"name": product.name},
        amount=Decimal("10.01"),
        term_months=1,
        annual_rate_percent=Decimal("12345.1234567890123456789"),
    )
    session.add(offer)
    await session.flush()
    return owner, company, product, app, other, result, audit, document, offer


async def test_partial_draft_and_exact_money_utc_json_date(session):
    owner = User(login=uuid4().hex, password_hash="test-hash", display_name="Тест", role="client")
    session.add(owner)
    await session.flush()
    draft = Application(owner_id=owner.id)
    session.add(draft)
    await session.commit()
    draft_id = draft.id
    # A new session verifies a real commit and read, including nullable fields/defaults.
    async with create_session_factory(session.bind)() as reader:
        saved = await reader.get(Application, draft_id)
        assert saved.status == "DRAFT" and saved.revision == 1
        assert all(
            getattr(saved, field) is None
            for field in (
                "company_id",
                "product_id",
                "company_snapshot",
                "product_snapshot",
                "amount",
                "term_months",
                "purpose",
                "annual_revenue",
                "current_decision_id",
                "accepted_offer_id",
            )
        )
        assert saved.created_at.utcoffset() == saved.updated_at.utcoffset() == timedelta(0)
        saved.amount = Decimal("999999999999999999999999999.99")
        saved.annual_revenue = Decimal("0.00")
        saved.term_months = 1
        saved.company_snapshot = {"inn": "1234567890", "name": "Тест"}
        saved.product_snapshot = {"code": "DEMO", "purposes": ["Тест"]}
        saved.updated_at = datetime(2026, 10, 21, 12, tzinfo=timezone(timedelta(hours=3)))
        await reader.commit()
    session.expunge_all()
    saved = await session.get(Application, draft_id)
    assert saved.amount == Decimal("999999999999999999999999999.99")
    assert isinstance(saved.amount, Decimal)
    assert saved.annual_revenue == Decimal("0.00")
    assert saved.updated_at == datetime(2026, 10, 21, 9, tzinfo=UTC)
    assert saved.company_snapshot["name"] == "Тест"
    company = Company(
        inn="123456789012", name="ИП", legal_form="IP", registration_date=date(2020, 1, 2)
    )
    session.add(company)
    await session.flush()
    await session.refresh(company)
    assert company.registration_date == date(2020, 1, 2)


async def test_valid_links_and_unscaled_rate(session, graph):
    _, _, _, app, _, result, _, _, offer = graph
    app.current_decision = result
    app.accepted_offer = offer
    await session.flush()
    await session.refresh(app, ["current_decision", "accepted_offer"])
    await session.refresh(offer)
    assert app.current_decision_id == result.id
    assert app.accepted_offer_id == offer.id
    assert app.current_decision.id == result.id
    assert app.accepted_offer.id == offer.id
    assert offer.annual_rate_percent == Decimal("12345.1234567890123456789")


@pytest.mark.parametrize("kind", ["login", "inn", "code", "sequence", "offer_decision"])
async def test_unique_constraints(session, graph, kind):
    owner, company, product, app, _, result, _, _, _ = graph
    duplicate = {
        "login": lambda: User(
            login=owner.login, password_hash="x", display_name="x", role="client"
        ),
        "inn": lambda: Company(inn=company.inn, name="x", legal_form="IP"),
        "code": lambda: Product(code=product.code, name="x", description="x", purposes=[]),
        "sequence": lambda: event(app.id),
        "offer_decision": lambda: Offer(
            application_id=app.id,
            decision_id=result.id,
            product_id=product.id,
            product_snapshot={},
            amount=Decimal("1.00"),
            term_months=1,
        ),
    }[kind]()
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(duplicate)
            await session.flush()
    assert exc.value.orig.sqlstate == "23505"


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "role", "admin"),
        (1, "inn", "bad"),
        (1, "legal_form", "BAD"),
        (2, "version", 0),
        (3, "amount", Decimal("0.00")),
        (3, "amount", Decimal("-0.01")),
        (3, "term_months", 0),
        (3, "annual_revenue", Decimal("-0.01")),
        (3, "revision", 0),
        (3, "status", "UNKNOWN"),
        (5, "input_revision", 0),
        (5, "revision", 0),
        (5, "outcome", "UNKNOWN"),
        (5, "source", "UNKNOWN"),
        (6, "sequence", 0),
        (6, "revision", 0),
        (6, "event_type", "UNKNOWN"),
        (6, "actor_kind", "UNKNOWN"),
        (6, "previous_status", "UNKNOWN"),
        (6, "status", "UNKNOWN"),
        (7, "application_revision", 0),
        (7, "size_bytes", -1),
        (8, "amount", Decimal("0.00")),
        (8, "term_months", 0),
        (8, "annual_rate_percent", Decimal("-0.0001")),
    ],
)
async def test_checks(session, graph, index, field, value):
    row = graph[index]
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.execute(
                update(type(row)).where(type(row).id == row.id).values({field: value})
            )
    assert exc.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    "index,field",
    [
        (3, "owner_id"),
        (3, "company_id"),
        (3, "product_id"),
        (3, "current_decision_id"),
        (3, "accepted_offer_id"),
        (5, "application_id"),
        (5, "author_id"),
        (6, "application_id"),
        (6, "actor_user_id"),
        (7, "application_id"),
        (7, "uploaded_by"),
        (8, "application_id"),
        (8, "decision_id"),
        (8, "product_id"),
    ],
)
async def test_foreign_keys(session, graph, index, field):
    row = graph[index]
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.execute(
                update(type(row)).where(type(row).id == row.id).values({field: uuid4()})
            )
    assert exc.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    "kind", ["current_decision", "accepted_offer", "offer_decision", "move_decision", "move_offer"]
)
async def test_cross_application_links_rejected(session, graph, kind):
    _, _, product, app, other, result, _, _, offer = graph
    if kind == "offer_decision":
        result = decision(app.id)
        session.add(result)
        await session.flush()
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            if kind == "current_decision":
                other.current_decision_id = result.id
            elif kind == "accepted_offer":
                other.accepted_offer_id = offer.id
            elif kind == "offer_decision":
                session.add(
                    Offer(
                        application_id=other.id,
                        decision_id=result.id,
                        product_id=product.id,
                        product_snapshot={},
                        amount=Decimal("1.00"),
                        term_months=1,
                    )
                )
            elif kind == "move_decision":
                result.application_id = other.id
            else:
                app.accepted_offer_id = offer.id
                await session.flush()
                offer.application_id = other.id
            await session.flush()
    assert exc.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("child_index", [5, 6, 8])
async def test_history_blocks_application_delete(session, graph, child_index):
    owner, _, _, app, _, _, _, _, _ = graph
    # Isolate each history kind so every FK is independently shown to block deletion.
    isolated = Application(owner_id=owner.id)
    session.add(isolated)
    await session.flush()
    if child_index == 5:
        session.add(decision(isolated.id))
    elif child_index == 6:
        session.add(event(isolated.id))
    else:
        # Offer alone cannot exist without a decision, so check its FK metadata as well.
        fks = Offer.__table__.foreign_keys
        assert all(fk.ondelete is None for fk in fks)
        isolated = app
    await session.flush()
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.delete(isolated)
            await session.flush()
    assert exc.value.orig.sqlstate == "23503"
    assert await session.scalar(select(type(graph[child_index]).id)) is not None


async def test_referenced_history_and_owner_cannot_be_deleted(session, graph):
    owner, _, _, app, _, result, _, _, offer = graph
    app.current_decision_id = result.id
    app.accepted_offer_id = offer.id
    await session.flush()
    for row in (owner, result, offer):
        with pytest.raises(IntegrityError) as exc:
            async with session.begin_nested():
                await session.execute(delete(type(row)).where(type(row).id == row.id))
        assert exc.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    "index,field", [(3, "owner_id"), (5, "application_id"), (6, "request_id"), (8, "amount")]
)
async def test_required_columns(session, graph, index, field):
    row = graph[index]
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.execute(
                update(type(row)).where(type(row).id == row.id).values({field: None})
            )
    assert exc.value.orig.sqlstate == "23502"
