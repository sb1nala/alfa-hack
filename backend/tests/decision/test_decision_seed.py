"""Every synthetic company from seed/companies.json gives the outcome in docs/test-data.md."""

from datetime import date
from decimal import Decimal

import pytest

from app.decision import decide
from tests.decision.helpers import build_inputs, seed_companies

SEED = seed_companies()
AS_OF = date.fromisoformat(SEED["_meta"]["as_of"])
COMPANIES = {company["key"]: company for company in SEED["companies"]}

# key: (outcome, reason codes, offer (product, amount, term, rate) or None, class, score)
EXPECTED = {
    "approve": ("APPROVED", (), ("working_capital_line", "5000000.00", 18, "23.0"), "A", 175),
    "counter_term": (
        "COUNTER_OFFER",
        ("term_extended",),
        ("express", "2500000.00", 24, "27.5"),
        "B",
        130,
    ),
    "counter_amount": (
        "COUNTER_OFFER",
        ("amount_reduced",),
        ("working_capital_line", "3710000.00", 24, "24.5"),
        "B",
        140,
    ),
    "counter_product": (
        "COUNTER_OFFER",
        ("product_changed", "collateral_required"),
        ("investment", "5000000.00", 84, "21.0"),
        "A",
        155,
    ),
    "needs_info": ("NEEDS_INFO", (), None, None, None),
    "manual": ("MANUAL_REVIEW", (), None, "D", 65),
    "reject_stop": ("REJECTED", ("company_liquidating",), None, None, None),
    "reject_score": ("REJECTED", ("high_risk",), None, "E", 30),
    "bki_failure": ("APPROVED", (), ("express", "1500000.00", 24, "26.0"), "A", 170),
}


def test_every_seed_company_has_an_expectation():
    assert set(COMPANIES) == set(EXPECTED)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_seed_company_outcome(key):
    company = COMPANIES[key]
    outcome, reasons, offer, risk_class, score = EXPECTED[key]

    decision = decide(*build_inputs(company, AS_OF))

    assert decision.outcome == company["expected_outcome"] == outcome
    assert tuple(reason.code for reason in decision.reasons) == reasons
    assert decision.risk_class == risk_class
    assert decision.score == score
    if offer is None:
        assert decision.offer is None
    else:
        product, amount, term, rate = offer
        assert decision.offer is not None
        assert decision.offer.product_code == product
        assert decision.offer.amount == Decimal(amount)
        assert decision.offer.term_months == term
        assert decision.offer.annual_rate_percent == rate
        assert decision.offer.monthly_payment > 0


def test_needs_info_requests_revenue_and_statement():
    decision = decide(*build_inputs(COMPANIES["needs_info"], AS_OF))
    assert [doc.code for doc in decision.required_documents] == [
        "annual_revenue",
        "bank_statement_12m",
    ]
    assert all(doc.title and doc.explanation for doc in decision.required_documents)


def test_needs_info_after_revenue_corrected():
    company = COMPANIES["needs_info"]
    after = company["expected_after_info"]
    decision = decide(*build_inputs(company, AS_OF, application_changes=after["change"]))
    assert decision.outcome == after["outcome"] == "APPROVED"
    assert decision.risk_class == "A" and decision.score == 150


def test_needs_info_with_statement_but_same_revenue_goes_to_analyst():
    decision = decide(
        *build_inputs(COMPANIES["needs_info"], AS_OF, document_types=("bank_statement_12m",))
    )
    assert decision.outcome == "MANUAL_REVIEW"
    assert decision.review_reason == "revenue_mismatch_review"


def test_manual_review_is_gray_zone():
    decision = decide(*build_inputs(COMPANIES["manual"], AS_OF))
    assert decision.review_reason == "gray_zone"


def test_stop_factor_skips_scoring():
    decision = decide(*build_inputs(COMPANIES["reject_stop"], AS_OF))
    assert decision.stop_factors == ("company_liquidating",)
    assert decision.scoring_factors == ()


def test_scored_decisions_explain_every_factor():
    decision = decide(*build_inputs(COMPANIES["approve"], AS_OF))
    assert len(decision.scoring_factors) == 10
    assert sum(int(f.contribution) for f in decision.scoring_factors) == decision.score
    assert decision.analytical_explanation
