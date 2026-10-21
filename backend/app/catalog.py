"""Local registry adapter and canonical company identities."""

import asyncio
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Product
from app.schemas.catalog import CompanyInput
from app.schemas.catalog import Product as PublicProduct


def public_product(product: Product) -> PublicProduct:
    return PublicProduct(
        id=product.id,
        code=product.code,
        name=product.name,
        description=product.description,
        purposes=product.purposes,
    )


async def company_identity(db: AsyncSession, company: CompanyInput, *, refresh=False) -> UUID:
    values = company.model_dump()
    query = insert(Company).values(**values)
    if refresh:
        query = query.on_conflict_do_update(
            index_elements=[Company.inn], set_={**values, "updated_at": func.clock_timestamp()}
        )
    else:
        # Form edits must not overwrite canonical registry data shared by other drafts.
        query = query.on_conflict_do_nothing(index_elements=[Company.inn])
    identity = await db.scalar(query.returning(Company.id))
    if identity is None:
        identity = await db.scalar(select(Company.id).where(Company.inn == company.inn))
    return identity


async def lookup_company(
    client: httpx.AsyncClient, inn: str, deadline_seconds: float
) -> CompanyInput | None:
    try:
        # Bound the complete operation too, including a response that trickles bytes.
        async with asyncio.timeout(deadline_seconds):
            response = await client.get(f"/egrul/{inn}", timeout=deadline_seconds)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        # The mock also sends status/director; neither belongs to public autofill.
        company = CompanyInput.model_validate(
            {field: data[field] for field in CompanyInput.model_fields if field in data}
        )
        if company.inn != inn:
            raise ValueError("Mismatched registry identity")
        return company
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, ValidationError):
        # Use the existing documented error, never confuse an upstream failure with 404.
        raise HTTPException(500) from None
