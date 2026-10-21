from typing import Annotated

from fastapi import APIRouter, Body

from app.api import examples as ex
from app.api.contract import endpoint, not_implemented
from app.schemas.catalog import CSRFResponse, LoginRequest, User

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE_HEADER = {
    "Set-Cookie": {
        "schema": {"type": "string"},
        "description": (
            "Будущая cookie access_token: HttpOnly; SameSite=Lax; Path=/; Secure при HTTPS. "
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
        "Привязка к HttpOnly csrf_context cookie, см. описание API. Сейчас токен не выдаётся.",
        role=None,
        headers={
            "Set-Cookie": {
                "schema": {"type": "string"},
                "description": "csrf_context; HttpOnly; SameSite=Lax; Path=/; Secure при HTTPS",
            },
            "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
        },
    ),
)
async def csrf() -> CSRFResponse:
    not_implemented()


@router.post(
    "/login",
    **endpoint(
        User,
        {
            "client": ex.USER,
            "underwriter": {**ex.USER, "login": "demo-bank", "role": "underwriter"},
        },
        "Вход по логину и паролю. Нужен CSRF-токен анонимного контекста. "
        "Будущий ответ — только пользователь, JWT устанавливается в HttpOnly cookie.",
        role=None,
        mutation=True,
        extra_errors=(401,),
        headers=COOKIE_HEADER,
    ),
)
async def login(
    body: Annotated[LoginRequest, Body(openapi_examples=ex.examples({"login": ex.LOGIN}))],
) -> User:
    not_implemented()


@router.post(
    "/logout",
    **endpoint(
        None,
        {},
        "Выход: будущая инвалидация сессии и очистка access_token/CSRF-контекста. "
        "После выхода снова получить CSRF-токен. Тело успешного ответа пустое.",
        role="both",
        mutation=True,
        status_code=204,
        headers=COOKIE_HEADER,
    ),
)
async def logout() -> None:
    not_implemented()


@router.get("/me", **endpoint(User, {"client": ex.USER}, "Текущий пользователь.", role="both"))
async def me() -> User:
    not_implemented()
