"""Russian display format of factor values and analytical texts (#81)."""

import math
from decimal import Decimal

import pytest

from app.decision import fmt

S = fmt.NBSP


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("80000.00"), f"80{S}000{S}₽"),
        (0, f"0{S}₽"),
        (999_999.4, f"999{S}999{S}₽"),
        (60_000_000.0, f"60{S}млн{S}₽"),
        (9_400_000, f"9,4{S}млн{S}₽"),
        (1_234_000_000, f"1{S}234{S}млн{S}₽"),
    ],
)
def test_rub(value, expected):
    assert fmt.rub(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (4.4, f"4,4{S}года"),
        (0.8, f"0,8{S}года"),
        (1.0, f"1{S}год"),
        (2.0, f"2{S}года"),
        (5.0, f"5{S}лет"),
        (11.0, f"11{S}лет"),
        (21.0, f"21{S}год"),
        (4.96, f"5{S}лет"),
    ],
)
def test_years(value, expected):
    assert fmt.years(value) == expected


def test_numbers_points_percent_rate():
    assert fmt.number(1.0612, 2) == "1,06"
    assert fmt.number(3_710_000) == f"3{S}710{S}000"
    assert fmt.number(math.inf, 2) == "—" and fmt.number(math.nan) == "—"
    assert fmt.rub_exact(3_710_000) == f"3{S}710{S}000{S}₽"
    assert [fmt.points(n) for n in (1, 2, 5, 11, 22, 65, 112)] == [
        f"1{S}балл",
        f"2{S}балла",
        f"5{S}баллов",
        f"11{S}баллов",
        f"22{S}балла",
        f"65{S}баллов",
        f"112{S}баллов",
    ]
    assert fmt.percent(0.13) == f"13{S}%" and fmt.percent(1.0) == f"100{S}%"
    assert fmt.rate("26.0") == f"26{S}%" and fmt.rate("24.5") == f"24,5{S}%"


def test_every_seed_decision_uses_russian_format():
    import re
    from datetime import date

    from app.decision import decide
    from tests.decision.helpers import build_inputs, seed_companies

    seed = seed_companies()
    as_of = date.fromisoformat(seed["_meta"]["as_of"])
    english = re.compile(r"\d\.\d|\d,\d{3}\b")
    for company in seed["companies"]:
        decision = decide(*build_inputs(company, as_of))
        # The OKVED code (43.21) keeps its official dotted form.
        values = [f.value for f in decision.scoring_factors if f.code != "industry_risk"]
        texts = values + [decision.analytical_explanation]
        for text in texts:
            assert not english.search(text), (company["key"], text)
