from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.api import examples as ex
from app.api.applications import ApplicationID
from app.api.contract import endpoint, not_implemented
from app.dependencies import accessible_application
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
        "Банковская очередь. По умолчанию MANUAL_REVIEW; фильтр status, пагинация offset/limit. "
        "Сортировка updated_at ASC, id ASC. Доступ только underwriter.",
        role="underwriter",
    ),
)
async def queue(
    page: Annotated[PageQuery, Depends()],
    status: ApplicationStatus = ApplicationStatus.MANUAL_REVIEW,
) -> Page[UnderwriterQueueItem]:
    not_implemented()


@router.get(
    "/{application_id}",
    **endpoint(
        UnderwriterApplication,
        {"bank_card": ex.BANK_APPLICATION},
        "Банковская карточка: клиентские данные + история решений с внутренними факторами "
        "и аналитическим объяснением. Никогда не использовать её схему в клиентском API.",
        role="underwriter",
        access=accessible_application,
    ),
)
async def bank_card(application_id: ApplicationID) -> UnderwriterApplication:
    not_implemented()


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
                    },
                ],
                "allowed_actions": [],
            }
        },
        "Ручное решение только из MANUAL_REVIEW: APPROVED, COUNTER_OFFER, NEEDS_INFO или REJECTED. "
        "Повторный MANUAL_REVIEW не является переходом в текущем документе. "
        "expected_revision обязателен. Решение добавляется в историю, старое не перезаписывается. "
        "explanation — клиенту, analytical_explanation — только банку.",
        role="underwriter",
        access=accessible_application,
        mutation=True,
    ),
)
async def manual_decision(
    application_id: ApplicationID,
    body: Annotated[ManualDecisionRequest, Body(openapi_examples=ex.examples(ex.MANUAL_REQUESTS))],
) -> UnderwriterApplication:
    not_implemented()
