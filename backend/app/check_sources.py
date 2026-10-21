"""Strict transport validation for local mocks; decision.from_dict is not a validator."""

from datetime import date
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.decision.types import BkiReport, EgrulReport, PbReport

Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,26})\.[0-9]{2}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Nonempty = Annotated[str, Field(min_length=1)]


class Transport(BaseModel):
    model_config = ConfigDict(strict=True)
    inn: Annotated[str, Field(pattern=r"^([0-9]{10}|[0-9]{12})$")]


class Registry(Transport):
    name: Nonempty
    legal_form: Literal["OOO", "IP"]
    status: Literal["ACTIVE", "LIQUIDATED", "LIQUIDATING", "BANKRUPT", "INACTIVE"]
    registration_date: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    okved: Annotated[str, Field(pattern=r"^[0-9]{2}(\.[0-9]{1,2}){0,2}$")]
    address: str | None
    director: str | None


class Tax(Transport):
    available: bool
    revenue: Money | None
    expenses: Money | None
    tax_arrears: Money
    mass_address: bool
    director_disqualified: bool
    employees: Count | None


class Loan(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Nonempty
    outstanding: Money
    monthly_payment: Money
    current_overdue_days: Count


class Bureau(Transport):
    loans: list[Loan]
    max_overdue_days_24m: Count
    overdue_30_plus_count_24m: Count
    inquiries_30d: Count


class SourceError(Exception):
    def __init__(self, stage: str, category: str, *, retryable: bool):
        # Never include a response body, URL, or exception text in logs/storage.
        super().__init__(category)
        self.stage = stage
        self.category = category
        self.retryable = retryable


def validate_response(stage: str, data: object, inn: str) -> dict:
    try:
        parsed = {"egrul": Registry, "pb": Tax, "bki": Bureau}[stage].model_validate(data)
        if parsed.inn != inn:
            raise ValueError("identity")
        values = parsed.model_dump()
        if stage == "egrul":
            date.fromisoformat(values["registration_date"])
            EgrulReport.from_dict(values)
        elif stage == "pb":
            if parsed.available and (parsed.revenue is None or parsed.expenses is None):
                raise ValueError("missing financials")
            PbReport.from_dict(values)
        else:
            BkiReport.from_dict(values)
        return data  # Store the original validated JSON, including source extension fields.
    except (ValidationError, ValueError, TypeError, KeyError):
        raise SourceError(stage, "invalid_data", retryable=False) from None


async def fetch_checks(client: httpx.AsyncClient, inn: str, request_seconds: float) -> dict:
    results = {}
    for stage, path, params in (
        ("egrul", f"/egrul/{inn}", None),
        ("pb", f"/pb/{inn}", None),
        ("bki", "/bki/report", {"inn": inn}),
    ):
        try:
            response = await client.get(path, params=params, timeout=request_seconds)
        except httpx.TimeoutException:
            raise SourceError(stage, "http_timeout", retryable=True) from None
        except httpx.RequestError:
            raise SourceError(stage, "network", retryable=True) from None
        if response.status_code != 200:
            raise SourceError(
                stage,
                f"http_{response.status_code}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        try:
            data = response.json()
        except ValueError:
            raise SourceError(stage, "invalid_json", retryable=False) from None
        results[stage] = validate_response(stage, data, inn)
    return results
