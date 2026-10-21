"""Credit policy parameters — docs/credit-policy.md.

Product limits must match ``seed/products.json``; tests enforce it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

POLICY_VERSION = "2026-10-21.draft1"


@dataclass(frozen=True)
class ProductRule:
    code: str
    min_amount: Decimal
    max_amount: Decimal
    min_term_months: int
    max_term_months: int
    base_rate_percent: Decimal
    collateral: bool
    auto_decision_max_amount: Decimal | None = None


PRODUCTS: dict[str, ProductRule] = {
    rule.code: rule
    for rule in (
        ProductRule(
            "express", Decimal("300000"), Decimal("5000000"), 3, 36, Decimal("26.0"), False
        ),
        ProductRule(
            "working_capital_line",
            Decimal("1000000"),
            Decimal("30000000"),
            3,
            24,
            Decimal("23.0"),
            False,
        ),
        ProductRule(
            "investment",
            Decimal("3000000"),
            Decimal("100000000"),
            12,
            84,
            Decimal("21.0"),
            True,
            auto_decision_max_amount=Decimal("15000000"),
        ),
        ProductRule(
            "overdraft", Decimal("100000"), Decimal("10000000"), 12, 12, Decimal("25.0"), False
        ),
    )
}

# Purpose → products in recommendation order (docs/product/ui-texts.ru.json, "purposes").
PURPOSES: dict[str, tuple[str, ...]] = {
    "buy_equipment": ("investment", "express"),
    "cash_gap": ("overdraft", "working_capital_line"),
    "purchase_goods": ("working_capital_line", "express"),
    "expand_business": ("investment",),
    "urgent_expenses": ("express", "overdraft"),
    "other": ("express", "working_capital_line", "investment", "overdraft"),
}

# Stop factors and data sufficiency
MIN_BUSINESS_AGE_MONTHS = 3
TAX_ARREARS_STOP = Decimal("300000")
REVENUE_DEVIATION_MAX = Decimal("0.30")

# Offer calculation
DSCR_MIN = 1.2
OVERDRAFT_REVENUE_SHARE = 0.5  # of average monthly revenue
TERM_STEP_MONTHS = 6
AMOUNT_ROUNDING = Decimal("10000")

# Risk classes: (min score, class), checked top-down
CLASS_THRESHOLDS: tuple[tuple[int, str], ...] = ((150, "A"), (120, "B"), (90, "C"), (60, "D"))
FALLBACK_CLASS = "E"
AUTO_CLASSES = ("A", "B", "C")
CLASS_RATE_ADDON: dict[str, Decimal] = {
    "A": Decimal("0.0"),
    "B": Decimal("1.5"),
    "C": Decimal("3.0"),
}
CLASS_REVENUE_SHARE: dict[str, float] = {"A": 0.5, "B": 0.4, "C": 0.25}

# Industry risk by the first OKVED group
LOW_RISK_OKVED = frozenset([f"{g:02d}" for g in range(1, 34)] + ["62", "63", "72", "85", "86"])
HIGH_RISK_OKVED = frozenset(["41", "42", "43", "55", "56", "79", "90", "91", "92", "93"])

# Bank-only codes for MANUAL_REVIEW (not yet in the product dictionary)
REVIEW_GRAY_ZONE = "gray_zone"
REVIEW_COLLATERAL = "collateral_review"
REVIEW_DOCUMENTS = "documents_review"
REVIEW_REVENUE_MISMATCH = "revenue_mismatch_review"
