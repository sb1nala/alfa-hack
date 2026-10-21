from urllib.parse import urlsplit

from pydantic import Field, SecretStr
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
