"""Inputs and result of the credit decision.

Input shapes match the mock responses in ``seed/companies.json`` (``mocks.egrul``,
``mocks.pb``, ``mocks.bki``): money is a rouble string with two decimals, as in the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

Outcome = Literal["APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "MANUAL_REVIEW", "REJECTED"]
LegalForm = Literal["OOO", "IP"]


def _money(value: Any) -> Decimal:
    return Decimal(str(value))


def _optional_money(value: Any) -> Decimal | None:
    return None if value is None else _money(value)


@dataclass(frozen=True)
class ApplicationInput:
    product_code: str
    purpose: str
    amount: Decimal
    term_months: int
    annual_revenue: Decimal | None
    legal_form: LegalForm
    submitted_on: date
    document_types: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplicationInput:
        submitted = data["submitted_on"]
        return cls(
            product_code=data["product_code"],
            purpose=data["purpose"],
            amount=_money(data["amount"]),
            term_months=int(data["term_months"]),
            annual_revenue=_optional_money(data.get("annual_revenue")),
            legal_form=data["legal_form"],
            submitted_on=submitted
            if isinstance(submitted, date)
            else date.fromisoformat(submitted),
            document_types=frozenset(data.get("document_types", ())),
        )


@dataclass(frozen=True)
class EgrulReport:
    """ЕГРЮЛ/ЕГРИП. ``status`` is ``ACTIVE`` for an operating company."""

    status: str
    registration_date: date
    okved: str
    address: str | None = None
    director: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EgrulReport:
        return cls(
            status=data["status"],
            registration_date=date.fromisoformat(data["registration_date"]),
            okved=data["okved"],
            address=data.get("address"),
            director=data.get("director"),
        )


@dataclass(frozen=True)
class PbReport:
    """ФНС «Прозрачный бизнес». ``available=False`` — no financial data for the last year."""

    available: bool
    revenue: Decimal | None
    expenses: Decimal | None
    tax_arrears: Decimal
    mass_address: bool
    director_disqualified: bool
    employees: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PbReport:
        return cls(
            available=bool(data.get("available", True)),
            revenue=_optional_money(data.get("revenue")),
            expenses=_optional_money(data.get("expenses")),
            tax_arrears=_money(data.get("tax_arrears", "0.00")),
            mass_address=bool(data.get("mass_address", False)),
            director_disqualified=bool(data.get("director_disqualified", False)),
            employees=data.get("employees"),
        )


@dataclass(frozen=True)
class BkiLoan:
    type: str
    outstanding: Decimal
    monthly_payment: Decimal
    current_overdue_days: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BkiLoan:
        return cls(
            type=data["type"],
            outstanding=_money(data["outstanding"]),
            monthly_payment=_money(data["monthly_payment"]),
            current_overdue_days=int(data.get("current_overdue_days", 0)),
        )


@dataclass(frozen=True)
class BkiReport:
    loans: tuple[BkiLoan, ...]
    max_overdue_days_24m: int
    overdue_30_plus_count_24m: int
    inquiries_30d: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BkiReport:
        return cls(
            loans=tuple(BkiLoan.from_dict(loan) for loan in data.get("loans", ())),
            max_overdue_days_24m=int(data["max_overdue_days_24m"]),
            overdue_30_plus_count_24m=int(data["overdue_30_plus_count_24m"]),
            inquiries_30d=int(data["inquiries_30d"]),
        )

    @property
    def monthly_payments(self) -> Decimal:
        return sum((loan.monthly_payment for loan in self.loans), Decimal("0"))


@dataclass(frozen=True)
class Reason:
    code: str
    message: str


@dataclass(frozen=True)
class RequiredDocument:
    code: str
    title: str
    explanation: str


@dataclass(frozen=True)
class OfferTerms:
    product_code: str
    amount: Decimal
    term_months: int
    annual_rate_percent: str
    monthly_payment: Decimal


@dataclass(frozen=True)
class ScoringFactor:
    code: str
    value: str
    contribution: str
    explanation: str


@dataclass(frozen=True)
class Decision:
    """Public part (client) + bank-only part (``score`` and below)."""

    outcome: Outcome
    reasons: tuple[Reason, ...]
    explanation: str
    required_documents: tuple[RequiredDocument, ...]
    offer: OfferTerms | None
    policy_version: str
    # Bank only: never sent to the client.
    score: int | None = None
    risk_class: str | None = None
    dscr: Decimal | None = None
    scoring_factors: tuple[ScoringFactor, ...] = ()
    stop_factors: tuple[str, ...] = ()
    review_reason: str | None = None
    analytical_explanation: str = ""
