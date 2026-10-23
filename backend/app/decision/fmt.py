"""Russian display format for factor values and analytical texts (issue #81).

Decimal comma, digit groups and units separated by a no-break space, plural forms.
Only for text shown to people; stored numbers and offer terms keep their own format.
"""

from __future__ import annotations

import math
from decimal import Decimal

NBSP = " "


def number(value: Decimal | float, places: int = 0) -> str:
    """1234567.891 -> '1 234 567,89'; non-finite -> '—'."""
    value = float(value)
    if not math.isfinite(value):
        return "—"
    text = f"{value:,.{places}f}"
    return text.replace(",", NBSP).replace(".", ",")


def short(value: Decimal | float, places: int = 1) -> str:
    """Drop a zero fraction: 60.0 -> '60', 9.4 -> '9,4'."""
    text = number(value, places)
    whole, _, fraction = text.partition(",")
    return whole if fraction.strip("0") == "" else text


def plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(count)
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def rub(value: Decimal | float) -> str:
    """Whole rubles below a million, millions with one decimal above: '80 000 ₽', '60 млн ₽'."""
    if abs(float(value)) >= 1e6:
        return f"{short(float(value) / 1e6)}{NBSP}млн{NBSP}₽"
    return f"{number(value)}{NBSP}₽"


def rub_exact(value: Decimal | float) -> str:
    """Exact whole rubles for limits and amounts: '3 710 000 ₽'."""
    return f"{number(value)}{NBSP}₽"


def years(value: float) -> str:
    """4.4 -> '4,4 года' (a fraction takes the genitive singular), 5.0 -> '5 лет'."""
    text = short(value)
    if "," in text:
        return f"{text}{NBSP}года"
    return f"{text}{NBSP}{plural(int(text.replace(NBSP, '')), 'год', 'года', 'лет')}"


def points(value: int) -> str:
    return f"{value}{NBSP}{plural(value, 'балл', 'балла', 'баллов')}"


def percent(share: float) -> str:
    """0.13 -> '13 %'."""
    return f"{number(share * 100)}{NBSP}%"


def rate(value: Decimal | float | str) -> str:
    """Annual rate '26.0' -> '26 %', '24.5' -> '24,5 %'."""
    return f"{short(Decimal(str(value)))}{NBSP}%"
