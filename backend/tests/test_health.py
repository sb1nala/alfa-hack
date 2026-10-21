import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    # A closed loopback port exercises the real asyncpg connection failure path.
    settings = Settings(
        _env_file=None,
        auth_jwt_secret="test-only-secret-of-at-least-32-characters",
        postgres_host="127.0.0.1",
        postgres_port=1,
        postgres_password="test-only",
        db_readiness_timeout=0.2,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_live_without_database(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_without_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert client.get("/health/live").status_code == 200


def test_swagger_and_openapi(client):
    assert client.get("/docs").status_code == 200
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {"/health/live", "/health/ready"} <= set(paths)
    assert "503" in paths["/health/ready"]["get"]["responses"]


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PASSWORD", "special:@/%password")
    settings = Settings(_env_file=None)
    assert settings.database_url.host == "db"
    assert settings.database_url.drivername == "postgresql+asyncpg"
    assert settings.database_url.password == "special:@/%password"
    assert "special" not in repr(settings)


@pytest.mark.parametrize("value", [0, -1, 11])
def test_invalid_readiness_timeout(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, postgres_password="test-only", db_readiness_timeout=value)


def test_password_is_required(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_runtime_requires_jwt_secret(monkeypatch):
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    settings = Settings(_env_file=None, postgres_password="test-only")
    with pytest.raises(ValueError, match="AUTH_JWT_SECRET is required"):
        with TestClient(create_app(settings)):
            pass


@pytest.mark.parametrize(
    "origin,secure",
    [
        ("https://example.test/", True),
        ("https://*.example.test", True),
        ("https://example.test", False),
        ("http://localhost:8000", True),
        ("https://user@example.test", True),
        ("https://example.test?other=1", True),
    ],
)
def test_runtime_rejects_unsafe_origin_and_cookie_config(origin, secure):
    settings = Settings(
        _env_file=None,
        postgres_password="test-only",
        auth_jwt_secret="test-only-secret-of-at-least-32-characters",
        auth_allowed_origin=origin,
        auth_cookie_secure=secure,
    )
    with pytest.raises(ValueError):
        settings.validate_auth_runtime()
