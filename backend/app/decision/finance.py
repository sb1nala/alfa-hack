"""Loan arithmetic: annuity payment, affordable principal, DSCR."""

from __future__ import annotations

import math

from app.decision.policy import DSCR_MIN


def annuity(principal: float, annual_rate_percent: float, months: int) -> float:
    r = annual_rate_percent / 100 / 12
    return principal * r / (1 - (1 + r) ** -months)


def max_principal(payment: float, annual_rate_percent: float, months: int) -> float:
    if payment <= 0:
        return 0.0
    r = annual_rate_percent / 100 / 12
    return payment * (1 - (1 + r) ** -months) / r


def monthly_service(
    product_code: str, amount: float, annual_rate_percent: float, months: int
) -> float:
    """Monthly debt service of the new loan.

    Overdraft: interest on the fully drawn limit. Credit line: treated as a fully drawn
    annuity loan (conservative).
    """
    if product_code == "overdraft":
        return amount * annual_rate_percent / 100 / 12
    return annuity(amount, annual_rate_percent, months)


def dscr(annual_cash_flow: float, existing_monthly: float, new_monthly: float) -> float:
    debt_service = 12 * (existing_monthly + new_monthly)
    return math.inf if debt_service <= 0 else annual_cash_flow / debt_service


def max_affordable(
    product_code: str,
    annual_cash_flow: float,
    existing_monthly: float,
    annual_rate_percent: float,
    months: int,
) -> float:
    """Largest new loan keeping DSCR >= DSCR_MIN."""
    free_payment = annual_cash_flow / 12 / DSCR_MIN - existing_monthly
    if product_code == "overdraft":
        return max(0.0, free_payment) / (annual_rate_percent / 100 / 12)
    return max_principal(free_payment, annual_rate_percent, months)
