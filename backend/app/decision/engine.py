"""decide(): credit decision by docs/credit-policy.md.

Pure function: no database, network or clock. The caller passes complete mock responses;
an unavailable external service is retried by the worker and never reaches decide().
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.decision import finance, fmt, texts
from app.decision.policy import (
    AMOUNT_ROUNDING,
    AUTO_CLASSES,
    CLASS_RATE_ADDON,
    CLASS_REVENUE_SHARE,
    MIN_BUSINESS_AGE_MONTHS,
    OVERDRAFT_REVENUE_SHARE,
    POLICY_VERSION,
    PRODUCTS,
    PURPOSES,
    REVENUE_DEVIATION_MAX,
    REVIEW_COLLATERAL,
    REVIEW_DOCUMENTS,
    REVIEW_GRAY_ZONE,
    REVIEW_INCOMPLETE,
    REVIEW_REVENUE_MISMATCH,
    TAX_ARREARS_STOP,
    TERM_STEP_MONTHS,
    ProductRule,
)
from app.decision.scorecard import ScoreResult, months_between, score
from app.decision.types import (
    ApplicationInput,
    BkiReport,
    Decision,
    EgrulReport,
    OfferTerms,
    PbReport,
    Reason,
    RequiredDocument,
)

_CENT = Decimal("0.01")
_EPSILON = 1e-6


@dataclass(frozen=True)
class _Finances:
    revenue: float
    cash_flow: float
    existing_monthly: float


def decide(
    application: ApplicationInput,
    bki: BkiReport,
    egrul: EgrulReport,
    pb: PbReport,
) -> Decision:
    if application.product_code not in PRODUCTS:
        raise ValueError(f"Unknown product: {application.product_code}")
    if application.amount <= 0 or application.term_months <= 0:
        # A malformed form is a validation error, not a credit-risk outcome.
        raise ValueError("Amount and term must be positive")

    stop = _stop_factors(application, bki, egrul, pb)
    if stop:
        return _rejected(
            stop,
            analytical=f"Стоп-факторы: {', '.join(stop)} (статус ЕГРЮЛ: {egrul.status}). "
            "Скоринг не проводился.",
        )

    sufficiency = _check_data(application, pb)
    if sufficiency is not None:
        return sufficiency

    gaps = _data_gaps(pb)
    if gaps:
        return Decision(
            outcome="MANUAL_REVIEW",
            reasons=(),
            explanation=texts.STATUS_DESCRIPTIONS["MANUAL_REVIEW"],
            required_documents=(),
            offer=None,
            policy_version=POLICY_VERSION,
            review_reason=REVIEW_INCOMPLETE,
            data_gaps=gaps,
            analytical_explanation=f"ФНС не вернула поля: {', '.join(gaps)}. "
            "Без них оценка неполная — скоринг не проводился.",
        )

    assert pb.revenue is not None and pb.expenses is not None  # guaranteed by _check_data
    fin = _Finances(
        revenue=float(pb.revenue),
        cash_flow=float(pb.revenue - pb.expenses),
        existing_monthly=float(bki.monthly_payments),
    )
    result = score(application, egrul, pb, bki, fin.revenue, fin.cash_flow)
    summary = (
        f"Класс {result.risk_class} ({fmt.points(result.score)}), "
        f"DSCR запроса {fmt.number(result.dscr, 2)} по базовой ставке."
    )

    if result.risk_class not in AUTO_CLASSES:
        if result.risk_class == "D":
            return _manual(REVIEW_GRAY_ZONE, result, f"{summary} Серая зона — решение аналитика.")
        return _rejected(("high_risk",), result, f"{summary} Риск выше допустимого.")

    product = PRODUCTS[application.product_code]
    if (
        product.auto_decision_max_amount is not None
        and application.amount > product.auto_decision_max_amount
    ):
        return _manual(
            REVIEW_COLLATERAL,
            result,
            f"{summary} Сумма выше автоматического лимита продукта — нужна оценка залога.",
        )
    return _terms(application, product, result, fin, summary)


def _stop_factors(
    application: ApplicationInput, bki: BkiReport, egrul: EgrulReport, pb: PbReport
) -> tuple[str, ...]:
    stop = []
    if egrul.status != "ACTIVE":
        stop.append("company_liquidating")
    if pb.director_disqualified is True:
        stop.append("director_disqualified")
    if any(loan.current_overdue_days >= 90 for loan in bki.loans):
        stop.append("overdue_90_plus")
    if months_between(egrul.registration_date, application.submitted_on) < MIN_BUSINESS_AGE_MONTHS:
        stop.append("business_too_young")
    if pb.tax_arrears is not None and pb.tax_arrears > TAX_ARREARS_STOP:
        stop.append("tax_debt")
    return tuple(stop)


def _check_data(application: ApplicationInput, pb: PbReport) -> Decision | None:
    """NEEDS_INFO when data is missing; MANUAL_REVIEW when documents need a human."""
    documents = application.document_types
    needed: list[str] = []
    review: tuple[str, str] | None = None

    tax_data_usable = (
        pb.available
        and pb.revenue is not None
        and pb.expenses is not None
        and pb.revenue > 0
        and pb.expenses >= 0
    )
    declared = application.annual_revenue
    if not tax_data_usable:
        document = "tax_return" if application.legal_form == "IP" else "financial_statements"
        if document in documents:
            review = (
                REVIEW_DOCUMENTS,
                "ФНС не вернула пригодные финансовые данные, клиент приложил документы.",
            )
        else:
            needed.append(document)
    if declared is None or declared <= 0:
        needed.append("annual_revenue")
    elif tax_data_usable:
        assert pb.revenue is not None
        deviation = abs(declared - pb.revenue) / pb.revenue
        if deviation > REVENUE_DEVIATION_MAX:
            if "bank_statement_12m" in documents:
                review = (
                    REVIEW_REVENUE_MISMATCH,
                    f"Выручка в анкете расходится с ФНС на {fmt.percent(deviation)}, "
                    "приложена выписка.",
                )
            else:
                needed += ["annual_revenue", "bank_statement_12m"]

    if pb.mass_address is True and "lease_agreement" not in documents:
        needed.append("lease_agreement")

    if needed:
        return Decision(
            outcome="NEEDS_INFO",
            reasons=(),
            explanation=texts.STATUS_DESCRIPTIONS["NEEDS_INFO"],
            required_documents=tuple(_document(code) for code in needed),
            offer=None,
            policy_version=POLICY_VERSION,
            analytical_explanation=f"Недостаточно данных: {', '.join(needed)}.",
        )
    if review is not None:
        return Decision(
            outcome="MANUAL_REVIEW",
            reasons=(),
            explanation=texts.STATUS_DESCRIPTIONS["MANUAL_REVIEW"],
            required_documents=(),
            offer=None,
            policy_version=POLICY_VERSION,
            review_reason=review[0],
            analytical_explanation=review[1],
        )
    return None


def _data_gaps(pb: PbReport) -> tuple[str, ...]:
    fields = ("tax_arrears", "mass_address", "director_disqualified")
    return tuple(name for name in fields if getattr(pb, name) is None)


def _terms(
    application: ApplicationInput,
    product: ProductRule,
    result: ScoreResult,
    fin: _Finances,
    summary: str,
) -> Decision:
    cls = result.risk_class
    amount, term = float(application.amount), application.term_months

    if _fits(product, amount, term, cls, fin):
        offer = _offer(product, application.amount, term, cls)
        return _with_offer(
            "APPROVED", (), offer, result, fin, f"{summary} Запрошенные условия проходят."
        )

    # 1. Same product and amount, longer term
    for longer in _longer_terms(term, product.max_term_months):
        if _fits(product, amount, longer, cls, fin):
            offer = _offer(product, application.amount, longer, cls)
            return _with_offer(
                "COUNTER_OFFER",
                ("term_extended",),
                offer,
                result,
                fin,
                f"{summary} На {term} мес. платёж не проходит, на {longer} мес. проходит.",
            )

    # 2. Another product for the same purpose, same amount, its maximum term
    for code in PURPOSES.get(application.purpose, ()):
        if code == product.code:
            continue
        alternative = PRODUCTS[code]
        if _fits(alternative, amount, alternative.max_term_months, cls, fin):
            reasons = ("product_changed",) + (
                ("collateral_required",) if alternative.collateral else ()
            )
            offer = _offer(alternative, application.amount, alternative.max_term_months, cls)
            return _with_offer(
                "COUNTER_OFFER",
                reasons,
                offer,
                result,
                fin,
                f"{summary} Продукт {product.code} не проходит, подходит {code}.",
            )

    # 3. Same product, reduced amount at the maximum term
    rate = float(product.base_rate_percent + CLASS_RATE_ADDON[cls])
    revenue_limit = _revenue_limit(product, cls, fin)
    dscr_limit = finance.max_affordable(
        product.code, fin.cash_flow, fin.existing_monthly, rate, product.max_term_months
    )
    reduced = _round_down(
        min(float(product.max_amount), float(application.amount), revenue_limit, dscr_limit)
    )
    # Rounding must not push the offer over a limit: step down until every check passes.
    while reduced >= product.min_amount and not _fits(
        product, float(reduced), product.max_term_months, cls, fin
    ):
        reduced -= AMOUNT_ROUNDING
    if reduced >= product.min_amount:
        offer = _offer(product, reduced, product.max_term_months, cls)
        return _with_offer(
            "COUNTER_OFFER",
            ("amount_reduced",),
            offer,
            result,
            fin,
            f"{summary} Максимальная сумма по лимитам — {fmt.rub_exact(reduced)} "
            f"на {product.max_term_months} мес.",
        )

    # 4. Nothing fits
    code = "high_debt_load" if dscr_limit < revenue_limit else "insufficient_revenue"
    return _rejected(
        (code,),
        result,
        f"{summary} Подходящих условий нет: лимит по DSCR {fmt.rub_exact(dscr_limit)}, "
        f"по выручке {fmt.rub_exact(revenue_limit)}.",
    )


def _fits(product: ProductRule, amount: float, term: int, cls: str, fin: _Finances) -> bool:
    if not (float(product.min_amount) <= amount <= float(product.max_amount)):
        return False
    if not (product.min_term_months <= term <= product.max_term_months):
        return False
    if product.auto_decision_max_amount is not None and amount > float(
        product.auto_decision_max_amount
    ):
        return False
    if amount > _revenue_limit(product, cls, fin) + _EPSILON:
        return False
    rate = float(product.base_rate_percent + CLASS_RATE_ADDON[cls])
    affordable = finance.max_affordable(
        product.code, fin.cash_flow, fin.existing_monthly, rate, term
    )
    return amount <= affordable + _EPSILON


def _revenue_limit(product: ProductRule, cls: str, fin: _Finances) -> float:
    if product.code == "overdraft":
        return fin.revenue / 12 * OVERDRAFT_REVENUE_SHARE
    return fin.revenue * CLASS_REVENUE_SHARE[cls]


def _longer_terms(term: int, max_term: int) -> list[int]:
    terms = list(range(term + TERM_STEP_MONTHS, max_term + 1, TERM_STEP_MONTHS))
    if term < max_term and (not terms or terms[-1] != max_term):
        terms.append(max_term)
    return terms


def _round_down(amount: float) -> Decimal:
    if not math.isfinite(amount) or amount <= 0:
        return Decimal("0")
    units = Decimal(str(amount)) / AMOUNT_ROUNDING
    return (units.to_integral_value(rounding=ROUND_FLOOR) * AMOUNT_ROUNDING).quantize(_CENT)


def _offer(product: ProductRule, amount: Decimal, term: int, cls: str) -> OfferTerms:
    rate = product.base_rate_percent + CLASS_RATE_ADDON[cls]
    payment = finance.monthly_service(product.code, float(amount), float(rate), term)
    return OfferTerms(
        product_code=product.code,
        amount=amount.quantize(_CENT),
        term_months=term,
        annual_rate_percent=f"{rate:.1f}",
        monthly_payment=Decimal(str(payment)).quantize(_CENT, rounding=ROUND_HALF_UP),
    )


def _with_offer(
    outcome: str,
    reason_codes: tuple[str, ...],
    offer: OfferTerms,
    result: ScoreResult,
    fin: _Finances,
    analytical: str,
) -> Decision:
    reasons = tuple(Reason(code, texts.COUNTER_OFFER_REASONS[code]) for code in reason_codes)
    offer_dscr = finance.dscr(fin.cash_flow, fin.existing_monthly, float(offer.monthly_payment))
    explanation = (
        texts.STATUS_DESCRIPTIONS["APPROVED"]
        if outcome == "APPROVED"
        else " ".join(reason.message for reason in reasons)
    )
    return Decision(
        outcome=outcome,  # type: ignore[arg-type]
        reasons=reasons,
        explanation=explanation,
        required_documents=(),
        offer=offer,
        policy_version=POLICY_VERSION,
        **_bank_part(result),
        offer_dscr=_two_places(offer_dscr),
        analytical_explanation=f"{analytical} DSCR предложения {fmt.number(offer_dscr, 2)} "
        f"по ставке {fmt.rate(offer.annual_rate_percent)}.",
    )


def _rejected(
    codes: tuple[str, ...], result: ScoreResult | None = None, analytical: str = ""
) -> Decision:
    reasons = tuple(Reason(code, texts.REJECTION_REASONS[code][0]) for code in codes)
    message, advice = texts.REJECTION_REASONS[codes[0]]
    is_stop = result is None
    return Decision(
        outcome="REJECTED",
        reasons=reasons,
        explanation=f"{message} {advice}",
        required_documents=(),
        offer=None,
        policy_version=POLICY_VERSION,
        **(_bank_part(result) if result is not None else {}),
        stop_factors=codes if is_stop else (),
        analytical_explanation=analytical,
    )


def _manual(review_reason: str, result: ScoreResult, analytical: str) -> Decision:
    return Decision(
        outcome="MANUAL_REVIEW",
        reasons=(),
        explanation=texts.STATUS_DESCRIPTIONS["MANUAL_REVIEW"],
        required_documents=(),
        offer=None,
        policy_version=POLICY_VERSION,
        **_bank_part(result),
        review_reason=review_reason,
        analytical_explanation=analytical,
    )


def _bank_part(result: ScoreResult) -> dict:
    return {
        "score": result.score,
        "risk_class": result.risk_class,
        "requested_dscr": _two_places(result.dscr),
        "scoring_factors": result.factors,
    }


def _two_places(value: float) -> Decimal | None:
    if not math.isfinite(value):
        return None
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _document(code: str) -> RequiredDocument:
    title, explanation = texts.REQUIRED_DOCUMENTS[code]
    return RequiredDocument(code=code, title=title, explanation=explanation)
