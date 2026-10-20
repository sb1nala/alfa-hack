import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from asyncpg import PostgresError
from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import create_engine, create_session_factory


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings()
        engine = create_engine(config)
        app.state.settings = config
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Умный кредитный кабинет предпринимателя", version="0.1.0", lifespan=lifespan
    )

    @app.get("/health/live", tags=["health"], response_model=HealthResponse)
    async def live() -> HealthResponse:
        """Process is running; no dependency checks."""
        return HealthResponse(status="ok")

    @app.get(
        "/health/ready",
        tags=["health"],
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse, "description": "Database is unavailable"}},
    )
    async def ready(request: Request, response: Response) -> HealthResponse:
        """Check PostgreSQL; cancellation may also require driver connection cleanup."""
        try:
            async with asyncio.timeout(request.app.state.settings.db_readiness_timeout):
                async with request.app.state.session_factory() as session:
                    await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="unavailable")
        return HealthResponse(status="ok")

    return app


app = create_app()
