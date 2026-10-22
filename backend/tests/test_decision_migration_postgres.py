"""Exercise only B08 up/down on a disposable DB with pre-existing public history."""

import asyncio
from uuid import uuid4

from conftest import migrate
from sqlalchemy import text

from app.database import create_engine


def test_b08_migration_preserves_decisions_offers_and_current_pointer(database):
    name = "b08_migrate_" + uuid4().hex
    settings = database.model_copy(update={"postgres_db": name})
    ids = {key: uuid4() for key in ("user", "product", "app", "decision", "offer", "submission")}

    async def administer(statement):
        engine = create_engine(database)
        try:
            async with engine.connect() as conn:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    async def execute(statements):
        engine = create_engine(settings)
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement), ids)
        finally:
            await engine.dispose()

    async def assert_history(b08):
        engine = create_engine(settings)
        try:
            async with engine.connect() as conn:
                assert (
                    await conn.scalar(text("SELECT current_decision_id FROM applications"))
                    == ids["decision"]
                )
                assert (
                    await conn.scalar(text("SELECT public_explanation FROM decisions"))
                    == "Legacy explanation"
                )
                assert await conn.scalar(text("SELECT amount::text FROM offers")) == "300000.00"
                columns = set(
                    (
                        await conn.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns WHERE "
                                "table_name='decisions'"
                            )
                        )
                    ).scalars()
                )
                assert (
                    ({"submission_id", "risk_result"} <= columns)
                    if b08
                    else not ({"submission_id", "risk_result"} & columns)
                )
                if b08:
                    assert (
                        await conn.scalar(
                            text(
                                "SELECT count(*) FROM decisions WHERE risk_result IS NULL AND "
                                "submission_id IS NULL"
                            )
                        )
                        == 1
                    )
        finally:
            await engine.dispose()

    asyncio.run(administer(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    try:
        migrate(settings, "upgrade", "b07_worker_checks")
        asyncio.run(
            execute(
                [
                    "INSERT INTO users(id,login,password_hash,display_name,role) VALUES "
                    "(:user,'legacy','hash','Legacy','client')",
                    "INSERT INTO products(id,code,name,description,purposes) VALUES "
                    "(:product,'legacy','Legacy','Legacy','[]')",
                    "INSERT INTO applications(id,owner_id,revision,input_revision,status) "
                    "VALUES (:app,:user,5,2,'APPROVED')",
                    "INSERT INTO decisions(id,application_id,input_revision,revision,"
                    "outcome,source,public_reasons,public_explanation,required_documents,"
                    "scoring_factors,analytical_explanation) VALUES (:decision,:app,2,5,"
                    "'APPROVED','automatic','[]','Legacy explanation','[]','[]','Legacy "
                    "analysis')",
                    "INSERT INTO offers(id,application_id,decision_id,product_id,"
                    "product_snapshot,amount,term_months) VALUES (:offer,:app,:decision,"
                    ":product,'{}',300000,12)",
                    "UPDATE applications SET current_decision_id=:decision WHERE id=:app",
                ]
            )
        )
        migrate(settings, "upgrade", "head")
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        asyncio.run(assert_history(True))
        asyncio.run(
            execute(
                [
                    "INSERT INTO application_submissions(id,application_id,input_revision,"
                    "form_snapshot,product_snapshot,consent_to_checks,submitted_at) VALUES "
                    "(:submission,:app,2,'{}','{}',true,now())",
                    "UPDATE decisions SET submission_id=:submission, "
                    "risk_result=jsonb_build_object('score',175,'dscr','1.23456789') WHERE "
                    "id=:decision",
                ]
            )
        )
        migrate(settings, "downgrade", "b07_worker_checks")
        asyncio.run(assert_history(False))
        migrate(settings, "upgrade", "head")
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        asyncio.run(assert_history(True))
    finally:
        asyncio.run(administer(f'DROP DATABASE "{name}" WITH (FORCE)'))
