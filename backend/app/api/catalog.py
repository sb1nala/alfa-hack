from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api import examples as ex
from app.api.contract import endpoint
from app.catalog import company_identity, lookup_company, public_product
from app.dependencies import DBSession
from app.models import Product
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
        implemented=True,
    ),
)
async def products(db: DBSession) -> ProductList:
    rows = await db.scalars(
        select(Product).where(Product.active).order_by(Product.code, Product.id)
    )
    return ProductList(items=[public_product(row) for row in rows])


@router.get(
    "/companies/lookup",
    **endpoint(
        CompanyLookup,
        {"found": {"company": ex.COMPANY}, "not_found": {"company": None}},
        "Автозаполнение по ИНН через локальный мок ЕГРЮЛ. "
        "Компания сохраняется в БД; заявка связывает её по ИНН. "
        "Компания не найдена — 200 с company=null; сбой источника — 500.",
        implemented=True,
    ),
)
async def company_lookup(
    inn: Annotated[INN, Query(examples=["7701234560"])], db: DBSession, request: Request
) -> CompanyLookup:
    # Authentication has already read the DB. End that transaction before network I/O.
    await db.commit()
    company = await lookup_company(
        request.app.state.registry, inn, request.app.state.settings.egrul_timeout
    )
    if company is not None:
        await company_identity(db, company, refresh=True)
        await db.commit()
    return CompanyLookup(company=company)
