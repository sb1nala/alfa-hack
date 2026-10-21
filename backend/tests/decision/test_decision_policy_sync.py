"""Policy constants and client texts stay in sync with seed/ and the product dictionary."""

from decimal import Decimal

from app.decision import POLICY_VERSION, texts
from app.decision.policy import PRODUCTS, PURPOSES
from tests.decision.helpers import load_json, seed_companies

SEED_PRODUCTS = load_json("seed/products.json")
UI = load_json("docs/product/ui-texts.ru.json")


def public(section: dict) -> dict:
    return {k: v for k, v in section.items() if not k.startswith("_")}


def test_policy_version_matches_seed():
    assert SEED_PRODUCTS["_meta"]["policy_version"] == POLICY_VERSION
    assert seed_companies()["_meta"]["policy_version"] == POLICY_VERSION


def test_products_match_seed():
    seed = {p["code"]: p for p in SEED_PRODUCTS["products"]}
    assert set(seed) == set(PRODUCTS)
    for code, rule in PRODUCTS.items():
        p = seed[code]
        assert Decimal(p["min_amount"]) == rule.min_amount
        assert Decimal(p["max_amount"]) == rule.max_amount
        assert p["min_term_months"] == rule.min_term_months
        assert p["max_term_months"] == rule.max_term_months
        assert Decimal(p["base_rate_percent"]) == rule.base_rate_percent
        assert p["collateral"] == rule.collateral
        auto = p["auto_decision_max_amount"]
        assert (Decimal(auto) if auto else None) == rule.auto_decision_max_amount


def test_purposes_match_seed_and_dictionary():
    assert {k: tuple(v) for k, v in SEED_PRODUCTS["purposes"].items()} == PURPOSES
    ui = {k: tuple(v["products"]) for k, v in public(UI["purposes"]).items()}
    assert ui == PURPOSES


def test_product_codes_are_in_dictionary():
    assert set(public(UI["products"])) == set(PRODUCTS)


def test_client_texts_match_dictionary():
    assert texts.REJECTION_REASONS == {
        k: (v["message"], v["advice"]) for k, v in public(UI["rejectionReasons"]).items()
    }
    assert texts.COUNTER_OFFER_REASONS == public(UI["counterOfferReasons"])
    assert texts.REQUIRED_DOCUMENTS == {
        k: (v["title"], v["explanation"]) for k, v in public(UI["requiredDocuments"]).items()
    }
    for status, description in texts.STATUS_DESCRIPTIONS.items():
        assert UI["statuses"][status]["description"] == description
