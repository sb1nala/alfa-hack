import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from app.api import examples as ex
from app.api.contract import endpoint
from app.dependencies import (
    CurrentPrincipal,
    DBSession,
    Principal,
    find_context,
    optional_principal,
    require_csrf,
)
from app.models import AuthSession, CSRFContext
from app.models import User as UserModel
from app.schemas.catalog import CSRFResponse, LoginRequest, User
from app.security import (
    context_digest,
    csrf_token,
    dummy_password_hash,
    encode_session,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE_HEADER = {
    "Set-Cookie": {
        "schema": {"type": "string"},
        "description": (
            "Cookie access_token: HttpOnly; SameSite=Lax; Path=/; Secure при HTTPS. "
            "JWT отсутствует в теле. CSRF-контекст ротируется."
        ),
    }
}


@router.get(
    "/csrf",
    **endpoint(
        CSRFResponse,
        {"token": {"csrf_token": "synthetic-csrf-example"}},
        "Получить CSRF-токен до входа или для текущей сессии. Cache-Control: no-store. "
        "Привязка к HttpOnly csrf_context cookie, см. описание API. "
        "Повторный вызов сохраняет токен действующего контекста.",
        role=None,
        implemented=True,
        extra_errors=(401,),
        headers={
            "Set-Cookie": {
                "schema": {"type": "string"},
                "description": "csrf_context; HttpOnly; SameSite=Lax; Path=/; Secure при HTTPS",
            },
            "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
        },
    ),
)
async def csrf(
    request: Request,
    response: Response,
    db: DBSession,
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> CSRFResponse:
    context = await find_context(request, db, principal)
    if context is None:
        context = await new_context(request, response, db, principal.session if principal else None)
        await db.commit()
    return CSRFResponse(
        csrf_token=csrf_token(request.app.state.settings, context.cookie_digest, context.session_id)
    )


@router.post(
    "/login",
    **endpoint(
        User,
        {
            "client": ex.USER,
            "underwriter": {**ex.USER, "login": "demo-bank", "role": "underwriter"},
        },
        "Вход по логину и паролю. Нужен CSRF-токен анонимного контекста. "
        "Ответ — только пользователь, JWT устанавливается в HttpOnly cookie.",
        role=None,
        implemented=True,
        mutation=True,
        extra_errors=(401,),
        headers=COOKIE_HEADER,
    ),
)
async def login(
    body: Annotated[LoginRequest, Body(openapi_examples=ex.examples({"login": ex.LOGIN}))],
    request: Request,
    response: Response,
    db: DBSession,
    context: Annotated[CSRFContext, Depends(require_csrf)],
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> User:
    user = await db.scalar(select(UserModel).where(UserModel.login == body.login))
    password_hash = user.password_hash if user else await run_in_threadpool(dummy_password_hash)
    valid = await run_in_threadpool(verify_password, password_hash, body.password)
    if not valid or user is None:
        raise HTTPException(401)
    now = datetime.now(UTC).replace(microsecond=0)
    if principal:
        principal.session.revoked_at = now
        await db.execute(delete(CSRFContext).where(CSRFContext.session_id == principal.session.id))
    else:
        await db.delete(context)
    session = AuthSession(
        user_id=user.id,
        role=user.role,
        created_at=now,
        expires_at=now + timedelta(hours=request.app.state.settings.auth_session_hours),
    )
    db.add(session)
    await db.flush()
    await new_context(request, response, db, session)
    token = encode_session(request.app.state.settings, user, session)
    await db.commit()
    set_cookie(request, response, "access_token", token, session.expires_at)
    return public_user(user)


@router.post(
    "/logout",
    **endpoint(
        None,
        {},
        "Выход: инвалидация сессии и очистка access_token/CSRF-контекста. "
        "После выхода снова получить CSRF-токен. Тело успешного ответа пустое.",
        role="both",
        implemented=True,
        mutation=True,
        status_code=204,
        headers=COOKIE_HEADER,
    ),
)
async def logout(
    request: Request, response: Response, db: DBSession, principal: CurrentPrincipal
) -> None:
    principal.session.revoked_at = datetime.now(UTC)
    await db.execute(delete(CSRFContext).where(CSRFContext.session_id == principal.session.id))
    await db.commit()
    for name in ("access_token", "csrf_context"):
        response.delete_cookie(
            name,
            path="/",
            secure=request.app.state.settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )


@router.get(
    "/me",
    **endpoint(User, {"client": ex.USER}, "Текущий пользователь.", role="both", implemented=True),
)
async def me(principal: CurrentPrincipal) -> User:
    return public_user(principal.user)


def public_user(user: UserModel) -> User:
    return User(id=user.id, login=user.login, display_name=user.display_name, role=user.role)


def set_cookie(request: Request, response: Response, name: str, value: str, expires: datetime):
    response.set_cookie(
        name,
        value,
        max_age=max(0, int((expires - datetime.now(UTC)).total_seconds())),
        expires=expires,
        httponly=True,
        samesite="lax",
        path="/",
        secure=request.app.state.settings.auth_cookie_secure,
    )


async def new_context(request: Request, response: Response, db, session: AuthSession | None):
    now = datetime.now(UTC)
    expires = (
        session.expires_at
        if session
        else now + timedelta(hours=request.app.state.settings.auth_session_hours)
    )
    cookie = secrets.token_urlsafe(32)
    context = CSRFContext(
        cookie_digest=context_digest(cookie),
        session_id=session.id if session else None,
        created_at=now,
        expires_at=expires,
    )
    db.add(context)
    set_cookie(request, response, "csrf_context", cookie, expires)
    return context
