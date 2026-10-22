from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_host: str = Field(default="localhost", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="alfa_hack", min_length=1)
    postgres_user: str = Field(default="alfa_hack", min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    db_readiness_timeout: float = Field(default=2.0, ge=0.1, le=10.0)

    # Optional for migration/seed tools; mandatory when the API lifespan starts.
    auth_jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    auth_jwt_issuer: str = Field(default="alfa-hack", min_length=1)
    auth_jwt_audience: str = Field(default="alfa-hack-web", min_length=1)
    auth_session_hours: int = Field(default=8, ge=1, le=168)
    auth_allowed_origin: str = "https://localhost"
    auth_cookie_secure: bool = True
    mocks_base_url: str = "http://localhost:8001"
    egrul_timeout: float = Field(default=2.0, ge=0.1, le=10.0)
    documents_dir: Path = Path("/data/documents")
    document_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)

    sse_poll_interval: float = Field(default=1, ge=0.05, le=5, allow_inf_nan=False)
    sse_access_interval: float = Field(default=5, ge=0.1, le=15, allow_inf_nan=False)
    sse_batch_size: int = Field(default=50, ge=1, le=100)
    sse_db_timeout: float = Field(default=5, ge=0.1, le=10, allow_inf_nan=False)
    sse_send_timeout: float = Field(default=10, ge=0.1, le=15, allow_inf_nan=False)

    worker_poll_interval: float = Field(default=1, ge=0.05, le=60, allow_inf_nan=False)
    worker_http_timeout: float = Field(default=3, ge=0.1, le=30, allow_inf_nan=False)
    worker_attempt_timeout: float = Field(default=30, ge=1, le=300, allow_inf_nan=False)
    worker_db_timeout: float = Field(default=5, ge=0.1, le=30, allow_inf_nan=False)
    worker_lease_seconds: float = Field(default=45, ge=2, le=600, allow_inf_nan=False)
    worker_max_attempts: int = Field(default=5, ge=1, le=20)
    worker_retry_base: float = Field(default=2, ge=0.1, le=300, allow_inf_nan=False)
    worker_retry_max: float = Field(default=30, ge=0.1, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_worker(self):
        if self.worker_lease_seconds < self.worker_attempt_timeout + self.worker_db_timeout + 5:
            raise ValueError("WORKER_LEASE_SECONDS must be >= ATTEMPT_TIMEOUT + DB_TIMEOUT + 5")
        if self.worker_retry_base > self.worker_retry_max:
            raise ValueError("WORKER_RETRY_BASE must be <= WORKER_RETRY_MAX")
        return self

    def auth_secret(self) -> str:
        if self.auth_jwt_secret is None:
            raise ValueError("AUTH_JWT_SECRET is required at runtime (at least 32 characters)")
        return self.auth_jwt_secret.get_secret_value()

    def validate_auth_runtime(self) -> None:
        self.auth_secret()
        origin = urlsplit(self.auth_allowed_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.netloc
            or origin.username is not None
            or origin.password is not None
            or origin.path
            or origin.query
            or origin.fragment
            or "*" in self.auth_allowed_origin
        ):
            raise ValueError("AUTH_ALLOWED_ORIGIN must be an exact http(s) origin without a path")
        if origin.scheme == "https" and not self.auth_cookie_secure:
            raise ValueError("HTTPS requires AUTH_COOKIE_SECURE=true")
        if origin.scheme == "http" and self.auth_cookie_secure:
            raise ValueError("Local HTTP requires explicit AUTH_COOKIE_SECURE=false")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
