"""OpenAPI metadata is declarative: no JWT, role or CSRF checks are performed."""

from typing import NoReturn

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.examples import examples
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
API v1 — контракт и типизированные заглушки. **Все валидные бизнес-запросы возвращают
501**, примеры успешных ответов предназначены для генерации типов и Prism.
JWT, роли, владение ресурсами и CSRF **только описаны**, проверки пока не реализованы.
Заглушки не создают данные, не устанавливают cookies и не выполняют интеграции.

Будущая авторизация: один origin для фронтенда и API. JWT только в cookie
`access_token` (HttpOnly; Secure в HTTPS; SameSite=Lax; Path=/), никогда в JSON или
localStorage. `GET /api/auth/csrf` доступен до входа: создаёт анонимный контекст
`csrf_context` в HttpOnly cookie с теми же атрибутами и возвращает `csrf_token`.
Клиент хранит CSRF-токен в памяти и передаёт `X-CSRF-Token` во **всех** изменяющих
запросах, включая login/logout. Сервер должен связать токен с контекстом/сессией,
проверять Origin и ротировать контекст и токен при входе/выходе. После входа/выхода
нужно снова получить CSRF-токен. GET не меняет бизнес-данные. Чужой ресурс — 404,
неверная роль или CSRF — 403, отсутствующая/просроченная сессия — 401.

UUID для идентификаторов, ISO 8601 UTC для времени, суммы — десятичные строки
рублей с двумя знаками, сроки — целые месяцы. `revision` начинается с 1;
каждая мутация заявки, включая загрузку документа, повышает её ревизию.
`expected_revision` проверяется атомарно; устаревшая ревизия и недопустимый статус —
409. После загрузки документа перечитать карточку для актуальной ревизии.
Ошибки: `code`, `message`, `field_errors` (пустой список вне валидации), `request_id`.
`X-Request-ID` ответа соответствует UUID в ошибке; секреты и входные значения
в ошибках не возвращаются. Клиент ориентируется на code, не на текст message.

Статусы/переходы — docs/application-states.md. Обнаружена неоднозначность: текст
разрешает CANCELLED «из любого статуса до SIGNED», диаграмма явно показывает лишь
COUNTER_OFFER → CANCELLED и объявляет REJECTED терминальным. Рекомендация для
следующего этапа: разрешить cancel из активных DRAFT, SUBMITTED, CHECKING, SCORING,
NEEDS_INFO, MANUAL_REVIEW, APPROVED, COUNTER_OFFER, OFFER_ACCEPTED, запретить из
REJECTED, SIGNED, ISSUED, CANCELLED; согласовать уточнение документа. Эти переходы
здесь не реализованы. `allowed_actions` — подсказка интерфейсу, сервер на следующем
этапе обязан повторно проверять права, статус и ревизию.

Все примеры синтетические. Ставки, пороги скоринга и кредитная политика не заданы.
Набор полей v1 — предложение этого PR, кроме явно согласованных решений задачи.
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
) -> dict:
    statuses = {400, 422, 429, 500, 501, *extra_errors}
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
    responses[status_code] = {"description": "Будущий успешный ответ (пока возвращается 501)"}
    if model:
        responses[status_code]["content"] = {"application/json": {"examples": examples(values)}}
    if headers:
        responses[status_code]["headers"] = headers
    security = {}
    if role:
        security["SessionCookie"] = []
    if mutation:
        security["CSRFToken"] = []
    return {
        "response_model": model,
        "status_code": status_code,
        "description": description + "\n\nЗаглушка: 501. JWT/роли/CSRF пока только в контракте.",
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
        headers={"X-Request-ID": request_id},
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
