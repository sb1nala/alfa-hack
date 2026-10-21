"""Published metadata and shared access dependencies for API operations."""

from collections.abc import Callable
from typing import NoReturn

from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.examples import examples
from app.dependencies import require_csrf, require_role
from app.schemas.common import ErrorResponse

ERRORS = {
    400: ("BAD_REQUEST", "Некорректный запрос"),
    401: ("UNAUTHENTICATED", "Требуется вход"),
    403: ("FORBIDDEN", "Недостаточно прав или неверный CSRF-токен"),
    404: ("NOT_FOUND", "Ресурс не найден или недоступен этому владельцу"),
    409: ("CONFLICT", "Конфликт состояния, ревизии или ключа идемпотентности"),
    410: ("EVENT_CURSOR_EXPIRED", "Курсор событий устарел; перечитайте карточку и историю"),
    413: ("FILE_TOO_LARGE", "Превышен допустимый размер документа"),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Неподдерживаемый тип документа"),
    422: ("VALIDATION_ERROR", "Некорректные параметры запроса"),
    429: ("RATE_LIMITED", "Слишком много запросов"),
    500: ("INTERNAL_ERROR", "Внутренняя ошибка"),
    501: ("NOT_IMPLEMENTED", "Контракт определён; бизнес-операция пока не реализована"),
}
DESCRIPTION = """
API v1: авторизация JWT, роли, владение ресурсами, Origin и CSRF реализованы.
Каталог продуктов, поиск компании через локальный мок ЕГРЮЛ, создание, список,
чтение и редактирование черновиков работают с PostgreSQL.
Остальные бизнес-операции остаются заглушками и после проверок доступа возвращают 501.

Один настроенный origin для фронтенда и API. JWT только в cookie `access_token`
(HttpOnly; Secure в HTTPS; SameSite=Lax; Path=/), никогда в JSON или localStorage.
GET /api/auth/csrf доступен до входа: создаёт анонимный контекст `csrf_context`
в HttpOnly cookie с теми же атрибутами и возвращает csrf_token. Клиент хранит токен
в памяти и передаёт X-CSRF-Token во всех изменяющих запросах, включая login/logout.
Origin обязателен и должен точно совпадать с AUTH_ALLOWED_ORIGIN. Токен связан с
серверным контекстом и сессией. При входе/выходе контекст ротируется, затем клиент
снова вызывает GET /api/auth/csrf; повторный GET сохраняет действующий токен.
Logout отзывает сессию в PostgreSQL, в том числе для скопированного JWT.
Auth-ответы имеют Cache-Control: no-store. Чужой ресурс — 404, неверная роль/CSRF
или Origin — 403, отсутствующая/просроченная/отозванная сессия — 401.

UUID для идентификаторов, ISO 8601 UTC для времени, суммы — десятичные строки
рублей с двумя знаками, сроки — целые месяцы. `revision` начинается с 1;
каждая мутация заявки, включая загрузку документа, повышает её ревизию.
`expected_revision` проверяется атомарно; устаревшая ревизия и недопустимый статус —
409. После загрузки документа перечитать карточку для актуальной ревизии.
Ошибки: `code`, `message`, `field_errors` (пустой список вне валидации), `request_id`.
`X-Request-ID` ответа соответствует UUID в ошибке; секреты и входные значения
в ошибках не возвращаются. Клиент ориентируется на code, не на текст message.

Статусы/переходы — docs/application-states.md. Отмена разрешена из активных
состояний до SIGNED, исключая конечные REJECTED, ISSUED, CANCELLED. Бизнес-переходы
здесь не реализованы. allowed_actions — подсказка интерфейсу; будущий сервис обязан
проверять права, статус и ревизию при выполнении операции.

Все примеры синтетические. Кредитные условия импортируются из справочника рисков;
публичный каталог не раскрывает внутренний снимок политики.
"""


def error_example(status: int) -> dict:
    code, message = ERRORS[status]
    return {
        "code": code,
        "message": message,
        "field_errors": (
            [
                {
                    "field": "body.term_months",
                    "code": "greater_than_equal",
                    "message": "Некорректное значение",
                }
            ]
            if status == 422
            else []
        ),
        "request_id": "80000000-0000-4000-8000-000000000001",
    }


def endpoint(
    model: type | None,
    values: dict,
    description: str,
    *,
    role: str | None = "client",
    mutation: bool = False,
    status_code: int = 200,
    extra_errors: tuple[int, ...] = (),
    headers: dict | None = None,
    implemented: bool = False,
    access: Callable | None = None,
) -> dict:
    statuses = {400, 422, 429, 500, 501, *extra_errors}
    if implemented:
        statuses.discard(501)
    if role:
        statuses.update({401, 403, 404})
    if mutation:
        statuses.update({403, 409})
    responses = {
        code: {
            "model": ErrorResponse,
            "description": ERRORS[code][1],
            "headers": {"X-Request-ID": {"schema": {"type": "string", "format": "uuid"}}},
            "content": {"application/json": {"examples": examples({"error": error_example(code)})}},
        }
        for code in sorted(statuses)
    }
    responses[status_code] = {
        "description": "Успешный ответ"
        if implemented
        else "Будущий успешный ответ (после проверок доступа пока 501)"
    }
    if model:
        responses[status_code]["content"] = {"application/json": {"examples": examples(values)}}
    if headers:
        responses[status_code]["headers"] = headers.copy()
    if implemented:
        for response in responses.values():
            response.setdefault("headers", {})["Cache-Control"] = {
                "schema": {"type": "string", "const": "no-store"}
            }
    security = {}
    if role:
        security["SessionCookie"] = []
    if mutation:
        security["CSRFToken"] = []
    dependencies = []
    if role:
        roles = ("client", "underwriter") if role == "both" else (role,)
        dependencies.append(Depends(require_role(*roles)))
    if mutation:
        dependencies.append(Depends(require_csrf))
    if access:
        dependencies.append(Depends(access))
    return {
        "dependencies": dependencies,
        "response_model": model,
        "status_code": status_code,
        "description": description
        + ("" if implemented else "\n\nБизнес-заглушка: 501 после проверок доступа."),
        "responses": responses,
        "openapi_extra": {
            "security": [security] if security else [],
            "x-required-roles": ["client", "underwriter"]
            if role == "both"
            else ([role] if role else []),
        },
    }


def not_implemented() -> NoReturn:
    raise HTTPException(status_code=501)


def error_response(request: Request, status: int, fields: list | None = None) -> JSONResponse:
    code, message = ERRORS.get(status, ("HTTP_ERROR", "Ошибка HTTP"))
    request_id = str(request.state.request_id)
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "field_errors": fields or [],
            "request_id": request_id,
        },
        headers={
            "X-Request-ID": request_id,
            **({"Cache-Control": "no-store"} if request.url.path.startswith("/api/auth/") else {}),
        },
    )


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not include exc.body, input, ctx or raw exception messages (passwords/files).
    fields = [
        {
            "field": ".".join(map(str, error["loc"])),
            "code": error["type"],
            "message": "Некорректное значение",
        }
        for error in exc.errors()
    ]
    return error_response(request, 422, jsonable_encoder(fields))
