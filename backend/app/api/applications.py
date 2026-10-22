from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select, true
from sqlalchemy.orm import aliased

from app import client_actions, drafts, submissions
from app.api import examples as ex
from app.api.contract import endpoint
from app.dependencies import (
    ApplicationID,
    ClientPrincipal,
    DBSession,
    accessible_application,
    owned_application,
)
from app.event_history import history_page
from app.models import Application
from app.schemas.applications import (
    AcceptOfferRequest,
    AdditionalInfoRequest,
    CancelRequest,
    ClientApplication,
    ClientApplicationSummary,
    CreateDraftRequest,
    EditDraftRequest,
    EventList,
    SignRequest,
    SubmitRequest,
)
from app.schemas.common import ApplicationStatus, Page, PageQuery
from app.transitions import lock_application

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.post(
    "",
    **endpoint(
        ClientApplication,
        {"draft": ex.APPLICATION},
        "Создать частичный черновик DRAFT, revision=1, владелец — текущий client. "
        "Все поля анкеты можно заполнить позднее; {} — допустимый черновик.",
        mutation=True,
        status_code=201,
        implemented=True,
        headers={
            "Location": {"schema": {"type": "string"}, "description": "/api/applications/{id}"}
        },
    ),
)
async def create_draft(
    body: Annotated[CreateDraftRequest, Body(openapi_examples=ex.examples({"draft": ex.FORM}))],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
    response: Response,
) -> ClientApplication:
    application = Application(owner_id=principal.user.id, status="DRAFT", revision=1)
    await drafts.apply_fields(db, application, body)
    db.add(application)
    await db.flush()
    await drafts.audit(db, application, principal, request.state.request_id, created=True)
    result = await drafts.card(db, application)
    await db.commit()
    response.headers["Location"] = f"/api/applications/{application.id}"
    return result


@router.get(
    "",
    **endpoint(
        Page[ClientApplicationSummary],
        {"applications": ex.page(ex.SUMMARY)},
        "Список только собственных заявок. Сортировка updated_at DESC, id DESC. "
        "offset/limit — пагинация; status — необязательный фильтр.",
        implemented=True,
    ),
)
async def list_applications(
    page: Annotated[PageQuery, Depends()],
    db: DBSession,
    principal: ClientPrincipal,
    status: ApplicationStatus | None = None,
) -> Page[ClientApplicationSummary]:
    filters = [Application.owner_id == principal.user.id]
    if status is not None:
        filters.append(Application.status == status.value)
    # A single statement gives total and page the same PostgreSQL MVCC snapshot,
    # including an empty page beyond the last row.
    filtered = select(Application).where(*filters).cte("filtered")
    paged = (
        select(filtered)
        .order_by(filtered.c.updated_at.desc(), filtered.c.id.desc())
        .offset(page.offset)
        .limit(page.limit)
        .cte("paged")
    )
    total = select(func.count().label("total")).select_from(filtered).cte("total")
    item = aliased(Application, paged)
    rows = (
        await db.execute(
            select(total.c.total, item)
            .select_from(total.outerjoin(paged, true()))
            .order_by(paged.c.updated_at.desc(), paged.c.id.desc())
        )
    ).all()
    return Page(
        items=[drafts.summary(row) for _, row in rows if row is not None],
        total=rows[0][0],
        offset=page.offset,
        limit=page.limit,
    )


@router.get(
    "/{application_id}",
    **endpoint(
        ClientApplication,
        ex.APPLICATION_EXAMPLES,
        "Карточка собственной заявки. Последнее публичное решение, status, revision, "
        "allowed_actions. "
        "Банковские факторы и аналитическое объяснение никогда не возвращаются.",
        access=owned_application,
        implemented=True,
    ),
)
async def get_application(
    application_id: ApplicationID,
    db: DBSession,
    application: Annotated[Application, Depends(owned_application)],
) -> ClientApplication:
    return await drafts.card(db, application)


@router.patch(
    "/{application_id}",
    **endpoint(
        ClientApplication,
        {"edited": {**ex.APPLICATION, "revision": 2}},
        "Редактировать анкету только в DRAFT. Меняются только переданные поля; null очищает поле. "
        "Требуется expected_revision; неверный статус или ревизия — 409.",
        mutation=True,
        access=owned_application,
        implemented=True,
    ),
)
async def edit_application(
    application_id: ApplicationID,
    body: Annotated[
        EditDraftRequest,
        Body(
            openapi_examples=ex.examples(
                {"edit": {**ex.REVISION, "purpose": "Пополнение оборотных средств"}}
            )
        ),
    ],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    if application.status != "DRAFT" or application.revision != body.expected_revision:
        raise HTTPException(409)
    await drafts.apply_fields(db, application, body)
    application.revision += 1
    application.updated_at = await db.scalar(select(func.clock_timestamp()))
    await db.flush()
    await drafts.audit(db, application, principal, request.state.request_id)
    result = await drafts.card(db, application)
    await db.commit()
    return result


@router.post(
    "/{application_id}/submit",
    **endpoint(
        ClientApplication,
        {
            "submitted": {
                **ex.APPLICATION,
                "status": "SUBMITTED",
                "revision": 2,
                "allowed_actions": ["cancel"],
            }
        },
        "Отправка DRAFT → SUBMITTED, 202: конвейер запустится асинхронно позднее. "
        "expected_revision обязателен. Idempotency-Key — UUID, генерирует клиент для "
        "одной попытки. "
        "Область ключа (пользователь, заявка, submit), хранение "
        "минимум 24 часа; "
        "тот же ключ + тот же JSON-запрос возвращает исходный 202 и снимок ответа "
        "без новой отправки "
        "(проверка повтора до ревизии); тот же ключ + другой запрос — 409; "
        "другой ключ при устаревшей ревизии/не-DRAFT — 409. Проверка и запись атомарны. "
        "Проверяются компания, активный продукт, цель, положительные сумма/срок и согласие. "
        "Выручка необязательна. Снимок входов, аудит, задание и ответ сохраняются одной "
        "транзакцией. В B06 заявка остаётся SUBMITTED до подключения worker B07.",
        mutation=True,
        status_code=202,
        access=owned_application,
        implemented=True,
    ),
)
async def submit_application(
    application_id: ApplicationID,
    body: Annotated[
        SubmitRequest,
        Body(openapi_examples=ex.examples({"submit": {**ex.REVISION, "consent_to_checks": True}})),
    ],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key", examples=[ex.EVENT_ID])],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    result = await submissions.submit(
        db, application_id, body, idempotency_key, principal, request.state.request_id
    )
    await db.commit()
    return result


@router.post(
    "/{application_id}/cancel",
    **endpoint(
        ClientApplication,
        {
            "cancelled": {
                **ex.APPLICATION,
                "status": "CANCELLED",
                "revision": 2,
                "allowed_actions": [],
            }
        },
        "Отмена активной заявки до SIGNED → CANCELLED согласно docs/application-states.md. "
        "Требуется expected_revision; конфликт статуса/ревизии — 409. "
        "Причина сохраняется во внутреннем аудите, ожидающее/захваченное задание "
        "инвалидируется атомарно. Worker обязан перепроверить актуальность результата.",
        mutation=True,
        access=owned_application,
        implemented=True,
    ),
)
async def cancel_application(
    application_id: ApplicationID,
    body: Annotated[CancelRequest, Body(openapi_examples=ex.examples({"cancel": ex.REVISION}))],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    result = await submissions.cancel(db, application_id, body, principal, request.state.request_id)
    await db.commit()
    return result


@router.post(
    "/{application_id}/accept-offer",
    **endpoint(
        ClientApplication,
        {
            "accepted": {
                **ex.APPLICATION_EXAMPLES["approved"],
                "status": "OFFER_ACCEPTED",
                "revision": 6,
                "allowed_actions": ["sign", "cancel"],
                "accepted_offer_id": ex.OFFER_ID,
            }
        },
        "Принять актуальный offer_id: APPROVED/COUNTER_OFFER → OFFER_ACCEPTED. "
        "expected_revision обязателен; чужое/устаревшее предложение или неверный статус — 409.",
        mutation=True,
        access=owned_application,
        implemented=True,
    ),
)
async def accept_offer(
    application_id: ApplicationID,
    body: Annotated[
        AcceptOfferRequest,
        Body(
            openapi_examples=ex.examples(
                {"accept": {"expected_revision": 5, "offer_id": ex.OFFER_ID}}
            )
        ),
    ],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    result = await client_actions.accept_offer(
        db, application_id, body, principal, request.state.request_id
    )
    await db.commit()
    return result


@router.post(
    "/{application_id}/additional-info",
    **endpoint(
        ClientApplication,
        {
            "checking": {
                **ex.APPLICATION_EXAMPLES["needs_info"],
                "status": "CHECKING",
                "revision": 6,
                "allowed_actions": ["cancel"],
            }
        },
        "Дополнительные данные: NEEDS_INFO → CHECKING. Документы предварительно загружаются, "
        "document_ids должны принадлежать этой заявке. expected_revision — после загрузок. "
        "annual_revenue, если передана, заменяет выручку в анкете. Создаётся новая версия "
        "входов: в проверку попадают типы всех документов заявки, задание конвейера "
        "ставится той же транзакцией. Последнее решение сохраняется до появления нового.",
        mutation=True,
        status_code=202,
        access=owned_application,
        implemented=True,
    ),
)
async def additional_info(
    application_id: ApplicationID,
    body: Annotated[
        AdditionalInfoRequest,
        Body(
            openapi_examples=ex.examples(
                {
                    "info": {
                        "expected_revision": 5,
                        "comment": "Документ добавлен",
                        "document_ids": [ex.DOCUMENT_ID],
                    }
                }
            )
        ),
    ],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    result = await client_actions.additional_info(
        db, application_id, body, principal, request.state.request_id
    )
    await db.commit()
    return result


@router.post(
    "/{application_id}/sign",
    **endpoint(
        ClientApplication,
        {
            "issued": {
                **ex.APPLICATION_EXAMPLES["approved"],
                "status": "ISSUED",
                "revision": 8,
                "allowed_actions": [],
                "accepted_offer_id": ex.OFFER_ID,
            }
        },
        "Симуляция подписания: OFFER_ACCEPTED → SIGNED клиентом, затем в той же транзакции "
        "системная симуляция выдачи SIGNED → ISSUED. Ответ — карточка в ISSUED, оба перехода "
        "в аудите. offer_id должен совпадать с принятым; иначе, как и при неверном статусе "
        "или ревизии, — 409.",
        mutation=True,
        access=owned_application,
        implemented=True,
    ),
)
async def sign(
    application_id: ApplicationID,
    body: Annotated[
        SignRequest,
        Body(
            openapi_examples=ex.examples(
                {"sign": {"expected_revision": 6, "offer_id": ex.OFFER_ID, "consent": True}}
            )
        ),
    ],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> ClientApplication:
    result = await client_actions.sign(
        db, application_id, body, principal, request.state.request_id
    )
    await db.commit()
    return result


@router.get(
    "/{application_id}/history",
    **endpoint(
        EventList,
        {"history": {"items": [ex.EVENT], "next_cursor": None}},
        "Публичная история по внутренней sequence заявки, включая события одной revision. "
        "cursor — исключительная нижняя граница; next_cursor — для следующей страницы. "
        "Неизвестный/чужой UUID — 404; 410 зарезервирован для подтверждённого истечения "
        "после согласования хранения, очистки/TTL сейчас нет. "
        "Внутренние факторы, авторы банковских решений и сырые ответы моков исключены.",
        role="both",
        extra_errors=(410,),
        access=accessible_application,
        implemented=True,
    ),
)
async def history(
    application_id: ApplicationID,
    db: DBSession,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EventList:
    return await history_page(db, application_id, cursor, limit)
