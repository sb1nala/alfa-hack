"""Individual rules of docs/credit-policy.md on a healthy base company."""

import copy
from datetime import date
from decimal import Decimal

import pytest

from app.decision import decide
from app.decision.finance import annuity
from app.decision.scorecard import industry_risk, months_between, risk_class
from tests.decision.helpers import build_inputs, seed_companies

AS_OF = date(2026, 10, 21)
BASE = next(c for c in seed_companies()["companies"] if c["key"] == "approve")


def run(changes=None, documents=(), application=None, legal_form=None, replace_pb=False):
    """replace_pb=True swaps the whole ФНС response (to drop fields) instead of updating it."""
    company = copy.deepcopy(BASE)
    for source, fields in (changes or {}).items():
        if source == "pb" and replace_pb:
            company["mocks"]["pb"] = dict(fields)
        else:
            company["mocks"][source].update(fields)
    if legal_form:
        company["company"]["legal_form"] = legal_form
    return decide(*build_inputs(company, AS_OF, application, documents))


def test_base_company_is_approved():
    assert run().outcome == "APPROVED"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"egrul": {"status": "LIQUIDATING"}}, "company_liquidating"),
        ({"pb": {"director_disqualified": True}}, "director_disqualified"),
        (
            {
                "bki": {
                    "loans": [
                        {
                            "type": "loan",
                            "outstanding": "100.00",
                            "monthly_payment": "10.00",
                            "current_overdue_days": 90,
                        }
                    ]
                }
            },
            "overdue_90_plus",
        ),
        ({"egrul": {"registration_date": "2026-08-01"}}, "business_too_young"),
        ({"pb": {"tax_arrears": "300000.01"}}, "tax_debt"),
    ],
)
def test_stop_factor_rejects_without_scoring(changes, code):
    decision = run(changes)
    assert decision.outcome == "REJECTED"
    assert [r.code for r in decision.reasons] == [code]
    assert decision.stop_factors == (code,)
    assert decision.score is None
    assert decision.explanation


def test_all_stop_factors_are_reported():
    decision = run({"egrul": {"status": "LIQUIDATING"}, "pb": {"director_disqualified": True}})
    assert [r.code for r in decision.reasons] == ["company_liquidating", "director_disqualified"]


def test_tax_arrears_at_threshold_is_not_a_stop_factor():
    decision = run({"pb": {"tax_arrears": "300000.00"}})
    assert decision.outcome == "APPROVED"
    factor = next(f for f in decision.scoring_factors if f.code == "tax_arrears")
    assert factor.contribution == "+0"


def test_business_exactly_three_months_old_passes_age_check():
    decision = run({"egrul": {"registration_date": "2026-07-21"}})
    assert "business_too_young" not in decision.stop_factors


@pytest.mark.parametrize(
    ("legal_form", "document"), [("OOO", "financial_statements"), ("IP", "tax_return")]
)
def test_missing_tax_data_requests_documents(legal_form, document):
    decision = run(
        {"pb": {"available": False, "revenue": None, "expenses": None}}, legal_form=legal_form
    )
    assert decision.outcome == "NEEDS_INFO"
    assert [d.code for d in decision.required_documents] == [document]


def test_missing_tax_data_with_documents_goes_to_analyst():
    decision = run(
        {"pb": {"available": False, "revenue": None, "expenses": None}},
        documents=("financial_statements",),
    )
    assert decision.outcome == "MANUAL_REVIEW"
    assert decision.review_reason == "documents_review"


def test_mass_address_requests_lease_then_scores_with_penalty():
    assert [d.code for d in run({"pb": {"mass_address": True}}).required_documents] == [
        "lease_agreement"
    ]
    decision = run({"pb": {"mass_address": True}}, documents=("lease_agreement",))
    factor = next(f for f in decision.scoring_factors if f.code == "mass_address")
    assert factor.contribution == "-15"


def test_several_gaps_are_requested_together():
    decision = run({"pb": {"mass_address": True}}, application={"annual_revenue": "90000000.00"})
    assert [d.code for d in decision.required_documents] == [
        "annual_revenue",
        "bank_statement_12m",
        "lease_agreement",
    ]


def test_revenue_within_30_percent_is_accepted():
    assert run(application={"annual_revenue": "62000000.00"}).outcome == "APPROVED"


def test_large_investment_loan_goes_to_collateral_review():
    decision = run(
        application={
            "product_code": "investment",
            "purpose": "buy_equipment",
            "amount": "16000000.00",
            "term_months": 60,
        }
    )
    assert decision.outcome == "MANUAL_REVIEW"
    assert decision.review_reason == "collateral_review"


def test_overdraft_above_limit_switches_to_credit_line():
    # Revenue 48 mln -> overdraft limit is 50% of monthly revenue = 2 mln < 3 mln requested.
    # Changing the product comes before reducing the amount.
    decision = run(
        application={
            "product_code": "overdraft",
            "purpose": "cash_gap",
            "amount": "3000000.00",
            "term_months": 12,
        }
    )
    assert decision.outcome == "COUNTER_OFFER"
    assert [r.code for r in decision.reasons] == ["product_changed"]
    assert decision.offer.product_code == "working_capital_line"
    assert decision.offer.amount == Decimal("3000000.00")
    assert decision.offer.term_months == 24


def test_unaffordable_loan_with_no_alternative_is_rejected():
    decision = run(
        {"pb": {"expenses": "47900000.00"}},
        application={
            "product_code": "investment",
            "purpose": "expand_business",
            "amount": "10000000.00",
            "term_months": 84,
        },
    )
    assert decision.outcome == "REJECTED"
    assert [r.code for r in decision.reasons] == ["high_debt_load"]
    assert decision.stop_factors == ()


def test_unknown_product_is_a_programming_error():
    with pytest.raises(ValueError):
        run(application={"product_code": "mortgage"})


def test_counter_offer_reduces_amount_and_hides_scores_from_client():
    decision = run(application={"amount": "29000000.00", "term_months": 6})
    assert decision.outcome == "COUNTER_OFFER"
    assert [r.code for r in decision.reasons] == ["amount_reduced"]
    assert decision.offer.amount == Decimal("8190000.00")
    assert decision.offer.amount % 10000 == 0
    assert "балл" not in decision.explanation and "DSCR" not in decision.explanation


@pytest.mark.parametrize(
    ("score", "expected"),
    [(150, "A"), (149, "B"), (120, "B"), (119, "C"), (90, "C"), (89, "D"), (60, "D"), (59, "E")],
)
def test_risk_class_thresholds(score, expected):
    assert risk_class(score) == expected


@pytest.mark.parametrize(
    ("okved", "risk"),
    [("10.71", "low"), ("62.01", "low"), ("46.69", "medium"), ("43.21", "high"), ("56.10", "high")],
)
def test_industry_risk(okved, risk):
    assert industry_risk(okved) == risk


def test_months_between_counts_full_months():
    assert months_between(date(2026, 7, 21), date(2026, 10, 21)) == 3
    assert months_between(date(2026, 7, 22), date(2026, 10, 21)) == 2


def test_annuity_payment():
    assert round(annuity(1_000_000, 12.0, 12), 2) == 88848.79
