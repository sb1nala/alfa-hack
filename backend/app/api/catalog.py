from typing import Annotated

from fastapi import APIRouter, Query

from app.api import examples as ex
from app.api.contract import endpoint, not_implemented
from app.schemas.catalog import CompanyLookup, ProductList
from app.schemas.common import INN

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get(
    "/products",
    **endpoint(
        ProductList,
        {"products": {"items": [ex.PRODUCT]}},
        "Список кредитных продуктов. Ставки и кредитные ограничения не определяются "
        "этим контрактом.",
        role="both",
    ),
)
async def products() -> ProductList:
    not_implemented()


@router.get(
    "/companies/lookup",
    **endpoint(
        CompanyLookup,
        {"found": {"company": ex.COMPANY}, "not_found": {"company": None}},
        "Автозаполнение по ИНН через локальные синтетические данные (интеграция позже). "
        "Проверяется только формат ИНН. Компания не найдена — 200 с company=null.",
    ),
)
async def company_lookup(inn: Annotated[INN, Query(examples=["7701234560"])]) -> CompanyLookup:
    not_implemented()
