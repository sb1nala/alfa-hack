import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-postgres",
        action="store_true",
        help="Run B03 tests in a newly created PostgreSQL 16 DB",
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"
