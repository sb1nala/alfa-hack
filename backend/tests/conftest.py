import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import Base


def pytest_addoption(parser):
    parser.addoption(
        "--run-postgres",
        action="store_true",
        help="Run database tests in a newly created PostgreSQL 16 DB",
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


@pytest.fixture(scope="session")
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

    async def job_columns():
        engine = create_engine(settings)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda conn: {c["name"] for c in inspect(conn).get_columns("pipeline_jobs")}
                )
        finally:
            await engine.dispose()

    async def legacy_data(insert=False):
        engine = create_engine(settings)
        try:
            async with engine.begin() as conn:
                if insert:
                    await conn.execute(
                        text(
                            "INSERT INTO users(id, login, password_hash, display_name, role) "
                            "VALUES (:id, 'b03-upgrade-check', 'legacy-hash', 'Demo', 'client')"
                        ),
                        {"id": uuid4()},
                    )
                else:
                    assert (
                        await conn.scalar(
                            text("SELECT password_hash FROM users WHERE login='b03-upgrade-check'")
                        )
                        == "legacy-hash"
                    )
        finally:
            await engine.dispose()

    asyncio.run(administer(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    try:
        migrate(settings, "upgrade", "b03_initial")
        asyncio.run(legacy_data(insert=True))
        migrate(settings, "upgrade", "head")
        asyncio.run(legacy_data())
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        migrate(settings, "downgrade", "b06_submit_queue")
        assert "submission_checks" not in set(asyncio.run(tables()))
        assert not {"phase", "last_error_stage", "last_error_category"} & asyncio.run(job_columns())
        asyncio.run(legacy_data())
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        migrate(settings, "downgrade", "b05_product_policy")
        assert not {"application_submissions", "pipeline_jobs", "submit_idempotency"} & set(
            asyncio.run(tables())
        )
        asyncio.run(legacy_data())
        migrate(settings, "upgrade", "head")
        migrate(settings, "check")
        migrate(settings, "downgrade", "b03_initial")
        asyncio.run(legacy_data())
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
