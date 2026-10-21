import asyncio
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import httpx
from asyncpg import PostgresError
from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import applications, auth, catalog, documents, events, underwriting
from app.api.contract import DESCRIPTION, error_response, validation_error
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.openapi import install_openapi


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings()
        config.validate_auth_runtime()
        engine = create_engine(config)
        app.state.settings = config
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            async with httpx.AsyncClient(
                base_url=config.mocks_base_url, trust_env=False, follow_redirects=False
            ) as registry:
                app.state.registry = registry
                yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Умный кредитный кабинет предпринимателя",
        version="0.2.0",
        lifespan=lifespan,
        description=DESCRIPTION,
        openapi_tags=[
            {"name": name}
            for name in (
                "auth",
                "catalog",
                "applications",
                "documents",
                "events",
                "underwriting",
                "health",
            )
        ],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid4()
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            response.headers["X-Request-ID"] = str(request.state.request_id)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_exception_handler(RequestValidationError, validation_error)

    @app.exception_handler(StarletteHTTPException)
    async def business_http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            return error_response(request, exc.status_code)
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return error_response(request, 500)

    routers = (
        auth.router,
        catalog.router,
        applications.router,
        documents.router,
        events.router,
        underwriting.router,
    )
    for router in routers:
        app.include_router(router)
    install_openapi(app, routers)

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
