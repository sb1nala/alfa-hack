"""Fixes from the risk review (seed/risk-review/credit-policy-review.md, section 11)."""

from datetime import date
from decimal import Decimal

import pytest

from app.decision import decide
from app.decision.policy import CLASS_REVENUE_SHARE, DSCR_MIN, OVERDRAFT_REVENUE_SHARE
from tests.decision.helpers import build_inputs, seed_companies
from tests.decision.test_decision_rules import run

SEED = seed_companies()
AS_OF = date.fromisoformat(SEED["_meta"]["as_of"])


# 1. Zero or invalid financial data is a data request, never a crash


def test_zero_tax_revenue_requests_documents_instead_of_crashing():
    decision = run({"pb": {"revenue": "0.00", "expenses": "0.00"}})
    assert decision.outcome == "NEEDS_INFO"
    assert [d.code for d in decision.required_documents] == ["financial_statements"]
    assert decision.score is None


def test_zero_revenue_everywhere_requests_documents_and_revenue():
    decision = run(
        {"pb": {"revenue": "0.00", "expenses": "0.00"}},
        application={"annual_revenue": "0.00"},
    )
    assert decision.outcome == "NEEDS_INFO"
    assert [d.code for d in decision.required_documents] == [
        "financial_statements",
        "annual_revenue",
    ]


def test_negative_tax_expenses_are_not_trusted():
    decision = run({"pb": {"expenses": "-1.00"}}, legal_form="IP")
    assert [d.code for d in decision.required_documents] == ["tax_return"]


def test_zero_declared_revenue_asks_for_revenue():
    decision = run(application={"annual_revenue": "0.00"})
    assert decision.outcome == "NEEDS_INFO"
    assert [d.code for d in decision.required_documents] == ["annual_revenue"]


@pytest.mark.parametrize("field", ["amount", "term_months"])
def test_non_positive_amount_or_term_is_a_validation_error(field):
    value = "0.00" if field == "amount" else 0
    with pytest.raises(ValueError):
        run(application={field: value})


# 2. A missing field never improves the assessment


@pytest.mark.parametrize("field", ["tax_arrears", "mass_address", "director_disqualified"])
def test_missing_tax_service_field_goes_to_analyst_without_scoring(field):
    company = {k: v for k, v in _base_pb().items() if k != field}
    decision = run({"pb": company}, replace_pb=True)
    assert decision.outcome == "MANUAL_REVIEW"
    assert decision.review_reason == "incomplete_checks"
    assert decision.data_gaps == (field,)
    assert decision.score is None and decision.scoring_factors == ()


def test_known_stop_factor_still_rejects_when_other_fields_are_missing():
    pb = {k: v for k, v in _base_pb().items() if k != "tax_arrears"}
    decision = run({"pb": pb, "egrul": {"status": "LIQUIDATING"}}, replace_pb=True)
    assert decision.outcome == "REJECTED"
    assert decision.stop_factors == ("company_liquidating",)


def test_client_data_request_comes_before_incomplete_checks():
    pb = {k: v for k, v in _base_pb().items() if k != "mass_address"}
    decision = run({"pb": pb}, application={"annual_revenue": "90000000.00"}, replace_pb=True)
    assert decision.outcome == "NEEDS_INFO"
    # Unknown mass address does not trigger a lease request
    assert "lease_agreement" not in [d.code for d in decision.required_documents]


# 3. Requested and offered DSCR are separate


@pytest.mark.parametrize(
    "key",
    [c["key"] for c in SEED["companies"] if c["expected_outcome"] in {"APPROVED", "COUNTER_OFFER"}],
)
def test_every_offer_passes_all_limits_after_rounding(key):
    company = next(c for c in SEED["companies"] if c["key"] == key)
    decision = decide(*build_inputs(company, AS_OF))
    assert decision.offer is not None
    assert decision.offer_dscr is not None and decision.offer_dscr >= Decimal(str(DSCR_MIN))
    revenue = Decimal(company["mocks"]["pb"]["revenue"])
    share = CLASS_REVENUE_SHARE[decision.risk_class]
    assert decision.offer.amount <= revenue * Decimal(str(share))


def test_counter_offer_shows_both_dscr_values():
    company = next(c for c in SEED["companies"] if c["key"] == "counter_amount")
    decision = decide(*build_inputs(company, AS_OF))
    assert decision.requested_dscr == Decimal("0.33")
    assert decision.offer_dscr >= Decimal("1.2")
    factor = next(f for f in decision.scoring_factors if f.code == "dscr")
    assert factor.value == "0.33"


def test_no_offer_means_no_offer_dscr():
    company = next(c for c in SEED["companies"] if c["key"] == "manual")
    decision = decide(*build_inputs(company, AS_OF))
    assert decision.requested_dscr is not None
    assert decision.offer_dscr is None


# 4. Overdraft repays principal within its term


def test_overdraft_payment_includes_principal():
    decision = run(
        application={
            "product_code": "overdraft",
            "purpose": "cash_gap",
            "amount": "1500000.00",
            "term_months": 12,
        }
    )
    assert decision.outcome == "APPROVED"
    interest_only = Decimal("1500000") * Decimal("25.0") / 100 / 12
    assert decision.offer.monthly_payment > interest_only * 3
    revenue = Decimal(_base_pb()["revenue"])
    assert decision.offer.amount <= revenue / 12 * Decimal(str(OVERDRAFT_REVENUE_SHARE))


# 5. Class C gets the class surcharge and the stricter revenue share


def test_class_c_regression():
    decision = run(
        {
            "egrul": {"registration_date": "2024-01-10"},
            "bki": {"max_overdue_days_24m": 20, "inquiries_30d": 4},
            "pb": {"tax_arrears": "50000.00"},
        }
    )
    assert decision.risk_class == "C"
    assert 90 <= decision.score <= 119
    assert decision.outcome == "APPROVED"
    assert decision.offer.annual_rate_percent == "26.0"


def _base_pb():
    company = next(c for c in SEED["companies"] if c["key"] == "approve")
    return dict(company["mocks"]["pb"])
