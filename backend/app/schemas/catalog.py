from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import INN, Role, Schema, Text


class LoginRequest(Schema):
    login: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024, json_schema_extra={"writeOnly": True})


class User(Schema):
    id: UUID
    login: str
    display_name: str
    role: Role


class CSRFResponse(Schema):
    csrf_token: str = Field(
        min_length=1, description="Передавать в X-CSRF-Token; JWT здесь отсутствует"
    )


class CompanyInput(Schema):
    inn: INN
    name: Text
    legal_form: Literal["OOO", "IP"]
    registration_date: date | None = None
    address: Text | None = None
    okved: str | None = Field(default=None, min_length=1, max_length=32)


class Company(CompanyInput):
    id: UUID


class Product(Schema):
    id: UUID
    code: str
    name: str
    description: str
    purposes: list[str]
    # Product rates/limits are intentionally not invented before policy agreement.


class ProductList(Schema):
    items: list[Product]


class CompanyLookup(Schema):
    company: CompanyInput | None = Field(
        description="null, если локальный справочник не нашёл компанию"
    )
