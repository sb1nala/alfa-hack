"""Shared authentication, CSRF, role and resource guards for API operations."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Request, Response
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import examples as ex
from app.models import Application, AuthSession, CSRFContext, Document, User
from app.security import context_digest, csrf_token, decode_session


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


DBSession = Annotated[AsyncSession, Depends(database_session)]
ApplicationID = Annotated[UUID, Path(examples=[ex.APPLICATION_ID])]


@dataclass
class Principal:
    user: User
    session: AuthSession


async def current_principal(request: Request, db: DBSession) -> Principal:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(401)
    try:
        claims = decode_session(request.app.state.settings, token)
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
        if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
            raise ValueError("Invalid timestamp type")
    except (InvalidTokenError, ValueError, TypeError, AttributeError):
        raise HTTPException(401) from None
    session = await db.get(AuthSession, session_id)
    user = await db.get(User, user_id)
    if (
        session is None
        or user is None
        or session.user_id != user.id
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
        or claims["role"] != session.role
        or user.role != session.role
        or claims["iat"] != int(session.created_at.timestamp())
        or claims["exp"] != int(session.expires_at.timestamp())
    ):
        raise HTTPException(401)
    return Principal(user, session)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def optional_principal(request: Request, db: DBSession) -> Principal | None:
    if "access_token" in request.cookies:
        return await current_principal(request, db)
    return None


async def csrf_principal(request: Request, response: Response, db: DBSession) -> Principal | None:
    """Recover stale browser credentials only on the anonymous CSRF bootstrap."""
    try:
        return await optional_principal(request, db)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        response.delete_cookie(
            "access_token",
            path="/",
            secure=request.app.state.settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return None


@lru_cache
def require_role(*roles: str):
    async def check(principal: CurrentPrincipal) -> Principal:
        if principal.user.role not in roles:
            raise HTTPException(403)
        return principal

    return check


ClientPrincipal = Annotated[Principal, Depends(require_role("client"))]


async def find_context(
    request: Request, db: AsyncSession, principal: Principal | None, *, lock=False
):
    cookie = request.cookies.get("csrf_context")
    if not cookie:
        return None
    query = select(CSRFContext).where(CSRFContext.cookie_digest == context_digest(cookie))
    if lock:
        query = query.with_for_update()
    context = await db.scalar(query)
    if (
        context is None
        or context.expires_at <= datetime.now(UTC)
        or context.session_id != (principal.session.id if principal else None)
    ):
        return None
    return context


async def require_csrf(
    request: Request,
    db: DBSession,
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> CSRFContext:
    config = request.app.state.settings
    # Header multiplicity is rejected too; never trust Host or forwarded headers.
    if request.headers.getlist("origin") != [config.auth_allowed_origin]:
        raise HTTPException(403)
    context = await find_context(request, db, principal, lock=True)
    tokens = request.headers.getlist("x-csrf-token")
    if context is None or len(tokens) != 1:
        raise HTTPException(403)
    expected = csrf_token(config, context.cookie_digest, context.session_id)
    if not secrets.compare_digest(tokens[0].encode(), expected.encode()):
        raise HTTPException(403)
    # Recheck the session after taking the context lock (a concurrent logout may revoke it).
    if principal:
        await db.refresh(principal.session)
        if principal.session.revoked_at is not None or principal.session.expires_at <= datetime.now(
            UTC
        ):
            raise HTTPException(401)
    return context


async def load_application(
    application_id: UUID, db: AsyncSession, principal: Principal, *, allow_underwriter: bool
) -> Application:
    application = await db.get(Application, application_id)
    if application is None or (
        application.owner_id != principal.user.id
        and not (allow_underwriter and principal.user.role == "underwriter")
    ):
        raise HTTPException(404)
    return application


async def owned_application(
    application_id: ApplicationID, db: DBSession, principal: CurrentPrincipal
):
    if principal.user.role != "client":
        raise HTTPException(403)
    return await load_application(application_id, db, principal, allow_underwriter=False)


async def accessible_application(
    application_id: ApplicationID, db: DBSession, principal: CurrentPrincipal
):
    return await load_application(application_id, db, principal, allow_underwriter=True)


async def application_document(
    document_id: Annotated[UUID, Path(examples=[ex.DOCUMENT_ID])],
    db: DBSession,
    application: Annotated[Application, Depends(accessible_application)],
) -> Document:
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id, Document.application_id == application.id
        )
    )
    if document is None:
        raise HTTPException(404)
    return document
