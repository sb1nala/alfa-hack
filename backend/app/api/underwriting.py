from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request

from app import underwriting
from app.api import examples as ex
from app.api.applications import ApplicationID
from app.api.contract import endpoint
from app.dependencies import DBSession, Principal, accessible_application, require_role
from app.models import Application
from app.schemas.common import ApplicationStatus, Page, PageQuery
from app.schemas.underwriting import (
    ManualDecisionRequest,
    UnderwriterApplication,
    UnderwriterQueueItem,
)

router = APIRouter(prefix="/api/underwriter/applications", tags=["underwriting"])


@router.get(
    "",
    **endpoint(
        Page[UnderwriterQueueItem],
        {"queue": ex.page(ex.QUEUE_ITEM)},
        "Банковская очередь. По умолчанию MANUAL_REVIEW. status можно повторить "
        "(?status=APPROVED&status=REJECTED) — заявки в любом из перечисленных статусов; "
        "пагинация offset/limit. Сортировка updated_at ASC, id ASC. Доступ только underwriter.",
        role="underwriter",
        implemented=True,
    ),
)
async def queue(
    page: Annotated[PageQuery, Depends()],
    db: DBSession,
    status: Annotated[
        list[ApplicationStatus],
        Query(min_length=1, max_length=len(ApplicationStatus), examples=[["MANUAL_REVIEW"]]),
    ] = [ApplicationStatus.MANUAL_REVIEW],  # noqa: B006 - FastAPI copies query defaults
) -> Page[UnderwriterQueueItem]:
    return await underwriting.queue(db, sorted({item.value for item in status}), page)


@router.get(
    "/{application_id}",
    **endpoint(
        UnderwriterApplication,
        {"bank_card": ex.BANK_APPLICATION},
        "Банковская карточка: клиентские данные + история решений с внутренними факторами "
        "и аналитическим объяснением. Никогда не использовать её схему в клиентском API.",
        role="underwriter",
        access=accessible_application,
        implemented=True,
    ),
)
async def bank_card(
    application_id: ApplicationID,
    db: DBSession,
    application: Annotated[Application, Depends(accessible_application)],
) -> UnderwriterApplication:
    return await underwriting.bank_card(db, application)


@router.post(
    "/{application_id}/decision",
    **endpoint(
        UnderwriterApplication,
        {
            "decided": {
                **ex.BANK_APPLICATION,
                "application": {
                    **ex.APPLICATION_EXAMPLES["approved"],
                    "revision": 6,
                    "decision": {
                        **ex.DECISIONS["approved"],
                        "id": "50000000-0000-4000-8000-000000000002",
                    },
                },
                "decisions": [
                    *ex.BANK_APPLICATION["decisions"],
                    {
                        "public": {
                            **ex.DECISIONS["approved"],
                            "id": "50000000-0000-4000-8000-000000000002",
                        },
                        "source": "underwriter",
                        "author_id": ex.USER_ID,
                        "policy_version": None,
                        "scoring_factors": [],
                        "analytical_explanation": "Внутреннее объяснение",
                        "review_reason": None,
                    },
                ],
                "allowed_actions": [],
            }
        },
        "Ручное решение только из MANUAL_REVIEW: APPROVED, COUNTER_OFFER, NEEDS_INFO или REJECTED. "
        "Повторный MANUAL_REVIEW не является переходом в текущем документе. "
        "expected_revision обязателен. Решение добавляется в историю, старое не перезаписывается. "
        "explanation — клиенту, analytical_explanation — только банку. "
        "Для APPROVED/COUNTER_OFFER product_id должен ссылаться на активный продукт (иначе 422); "
        "предложение сохраняется со снимком продукта. Автор решения — текущий underwriter.",
        role="underwriter",
        access=accessible_application,
        mutation=True,
        implemented=True,
    ),
)
async def manual_decision(
    application_id: ApplicationID,
    body: Annotated[ManualDecisionRequest, Body(openapi_examples=ex.examples(ex.MANUAL_REQUESTS))],
    db: DBSession,
    principal: Annotated[Principal, Depends(require_role("underwriter"))],
    request: Request,
) -> UnderwriterApplication:
    result = await underwriting.decide(
        db, application_id, body, principal, request.state.request_id
    )
    await db.commit()
    return result
