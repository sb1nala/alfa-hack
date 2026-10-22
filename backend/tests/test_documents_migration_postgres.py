"""Exercise only B11 up/down on a disposable DB with a pre-existing submission."""

import asyncio
from uuid import uuid4

from conftest import migrate
from sqlalchemy import text

from app.database import create_engine


def test_b11_migration_defaults_existing_submissions_to_no_documents(database):
    name = "b11_migrate_" + uuid4().hex
    settings = database.model_copy(update={"postgres_db": name})
    ids = {key: uuid4() for key in ("user", "app", "submission")}

    async def administer(statement):
        engine = create_engine(database)
        try:
            async with engine.connect() as conn:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    async def scalar(statement):
        engine = create_engine(settings)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), ids)
                return result.scalar() if result.returns_rows else None
        finally:
            await engine.dispose()

    asyncio.run(administer(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    try:
        migrate(settings, "upgrade", "b08_decision_result")
        for statement in (
            "INSERT INTO users(id,login,password_hash,display_name,role) VALUES "
            "(:user,'legacy','hash','Legacy','client')",
            "INSERT INTO applications(id,owner_id,revision,input_revision,status) "
            "VALUES (:app,:user,2,2,'SUBMITTED')",
            "INSERT INTO application_submissions(id,application_id,input_revision,"
            "form_snapshot,product_snapshot,consent_to_checks,submitted_at) VALUES "
            "(:submission,:app,2,'{}','{}',true,now())",
        ):
            asyncio.run(scalar(statement))
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        assert asyncio.run(scalar("SELECT document_types FROM application_submissions")) == []
        migrate(settings, "downgrade", "b08_decision_result")
        column = (
            "SELECT count(*) FROM information_schema.columns WHERE "
            "table_name='application_submissions' AND column_name='document_types'"
        )
        assert asyncio.run(scalar(column)) == 0
        assert asyncio.run(scalar("SELECT count(*) FROM application_submissions")) == 1
        migrate(settings, "upgrade", "head")
        assert asyncio.run(scalar(column)) == 1
    finally:
        asyncio.run(administer(f'DROP DATABASE "{name}" WITH (FORCE)'))
