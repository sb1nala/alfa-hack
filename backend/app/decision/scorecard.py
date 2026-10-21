"""Scorecard: 10 factors, -35..200 points, risk classes A–E (docs/credit-policy.md)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.decision import finance
from app.decision.policy import (
    CLASS_THRESHOLDS,
    FALLBACK_CLASS,
    HIGH_RISK_OKVED,
    LOW_RISK_OKVED,
    PRODUCTS,
)
from app.decision.types import ApplicationInput, BkiReport, EgrulReport, PbReport, ScoringFactor


@dataclass(frozen=True)
class ScoreResult:
    score: int
    risk_class: str
    dscr: float
    factors: tuple[ScoringFactor, ...]


def months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if end.day < start.day else months


def industry_risk(okved: str) -> str:
    group = okved.split(".")[0]
    if group in LOW_RISK_OKVED:
        return "low"
    if group in HIGH_RISK_OKVED:
        return "high"
    return "medium"


def risk_class(score: int) -> str:
    for threshold, name in CLASS_THRESHOLDS:
        if score >= threshold:
            return name
    return FALLBACK_CLASS


def _rub(value: Decimal | float) -> str:
    return f"{float(value) / 1e6:.1f} млн ₽"


def _factor(code: str, value: str, points: int, explanation: str) -> ScoringFactor:
    return ScoringFactor(
        code=code, value=value, contribution=f"{points:+d}", explanation=explanation
    )


def score(
    application: ApplicationInput,
    egrul: EgrulReport,
    pb: PbReport,
    bki: BkiReport,
    revenue: float,
    cash_flow: float,
) -> ScoreResult:
    factors: list[tuple[ScoringFactor, int]] = []

    def add(code: str, value: str, points: int, explanation: str) -> None:
        factors.append((_factor(code, value, points, explanation), points))

    age_years = months_between(egrul.registration_date, application.submitted_on) / 12
    points = 0 if age_years < 1 else 15 if age_years < 3 else 25 if age_years < 5 else 35
    add("business_age", f"{age_years:.1f} лет", points, "Возраст бизнеса по ЕГРЮЛ/ЕГРИП")

    points = 5 if revenue < 5e6 else 15 if revenue < 30e6 else 25 if revenue < 120e6 else 30
    add("revenue", _rub(revenue), points, "Годовая выручка по данным ФНС")

    # DSCR with the new loan at the product's base rate and the requested terms
    product = PRODUCTS[application.product_code]
    new_payment = finance.monthly_service(
        product.code,
        float(application.amount),
        float(product.base_rate_percent),
        application.term_months,
    )
    dscr = finance.dscr(cash_flow, float(bki.monthly_payments), new_payment)
    points = (
        0 if dscr < 1.0 else 10 if dscr < 1.2 else 25 if dscr < 1.5 else 35 if dscr < 2.0 else 45
    )
    add("dscr", f"{dscr:.2f}", points, "Покрытие платежей денежным потоком с новым кредитом")

    overdue = bki.max_overdue_days_24m
    points = 30 if overdue == 0 else 15 if overdue < 30 else 0
    add("max_overdue_24m", f"{overdue} дн.", points, "Максимальная просрочка за 24 месяца, БКИ")

    count = bki.overdue_30_plus_count_24m
    points = 10 if count == 0 else 0 if count == 1 else -15
    add("overdue_30_plus_count", str(count), points, "Число просрочек 30+ дней за 24 месяца, БКИ")

    inquiries = bki.inquiries_30d
    points = 10 if inquiries <= 2 else 0 if inquiries <= 5 else -10
    add("bki_inquiries_30d", str(inquiries), points, "Запросы кредитной истории за 30 дней, БКИ")

    points = 15 if pb.tax_arrears == 0 else 0
    add("tax_arrears", f"{pb.tax_arrears:.2f} ₽", points, "Налоговая задолженность по данным ФНС")

    points = -15 if pb.mass_address else 5
    add(
        "mass_address",
        "да" if pb.mass_address else "нет",
        points,
        "Массовый адрес регистрации, ФНС",
    )

    risk = industry_risk(egrul.okved)
    points = {"low": 10, "medium": 5, "high": 0}[risk]
    labels = {"low": "низкий", "medium": "средний", "high": "высокий"}
    add("industry_risk", f"{egrul.okved} ({labels[risk]})", points, "Отраслевой риск по ОКВЭД")

    share = float(application.amount) / revenue
    points = 10 if share <= 0.25 else 5 if share <= 0.5 else 0
    add("amount_to_revenue", f"{share:.0%}", points, "Запрошенная сумма к годовой выручке")

    total = sum(points for _, points in factors)
    return ScoreResult(
        score=total,
        risk_class=risk_class(total),
        dscr=dscr,
        factors=tuple(factor for factor, _ in factors),
    )
