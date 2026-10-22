"""B08 boundary: immutable inputs, policy compatibility and lossless result storage."""

import json
from dataclasses import asdict
from datetime import UTC
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.catalog import public_product
from app.check_sources import SourceError, validate_response
from app.decision import ApplicationInput, BkiReport, EgrulReport, PbReport, decide
from app.decision.policy import POLICY_VERSION, PRODUCTS, PURPOSES
from app.models import Decision, Offer, Product
from app.schemas.applications import DraftFields


def incompatible(category):
    return SourceError("decision", category, retryable=False)


def compatible_product(snapshot: dict) -> str:
    """Version equality is required; also detect drift under the same version label."""
    try:
        code = snapshot["code"]
        policy = snapshot["policy"]
        if policy["policy_version"] != POLICY_VERSION:
            raise incompatible("incompatible_policy")
        if code not in PRODUCTS or policy["code"] != code:
            raise incompatible("unsupported_product")
        for key, expected in asdict(PRODUCTS[code]).items():
            value = policy[key]
            if isinstance(expected, Decimal):
                value = Decimal(value) if isinstance(value, str) else None
            elif type(value) is not type(expected):
                raise incompatible("incompatible_policy")
            if value != expected:
                raise incompatible("incompatible_policy")
        UUID(snapshot["id"])
        return code
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise incompatible("invalid_product_snapshot") from None


def build_inputs(submission, checks):
    try:
        form = DraftFields.model_validate(submission.form_snapshot)
        if (
            form.company is None
            or form.product_id is None
            or form.amount is None
            or form.term_months is None
            or form.purpose is None
            or not submission.consent_to_checks
            or submission.submitted_at.tzinfo is None
            or not isinstance(submission.document_types, list)
            or not all(isinstance(item, str) for item in submission.document_types)
        ):
            raise ValueError("incomplete submission")
        code = compatible_product(submission.product_snapshot)
        if form.product_id != UUID(submission.product_snapshot["id"]):
            raise ValueError("product identity")
        if form.purpose not in PURPOSES:
            # Keep the original string in form_snapshot. No heuristic mapping/other fallback.
            raise incompatible("unsupported_purpose")
        if checks is None:
            raise incompatible("missing_checks")
        if checks.schema_version != 1:
            raise incompatible("unsupported_checks_schema")
        inn = form.company.inn
        reports = {
            stage: validate_response(stage, getattr(checks, stage), inn)
            for stage in ("bki", "egrul", "pb")
        }
        return (
            ApplicationInput(
                product_code=code,
                amount=Decimal(form.amount),
                term_months=form.term_months,
                purpose=form.purpose,
                annual_revenue=(Decimal(form.annual_revenue) if form.annual_revenue else None),
                legal_form=form.company.legal_form,
                submitted_on=submission.submitted_at.astimezone(UTC).date(),
                document_types=frozenset(submission.document_types),
            ),
            BkiReport.from_dict(reports["bki"]),
            EgrulReport.from_dict(reports["egrul"]),
            PbReport.from_dict(reports["pb"]),
        )
    except (ValidationError, KeyError, ValueError, TypeError, ArithmeticError):
        raise incompatible("invalid_inputs") from None


def calculate(submission, checks):
    inputs = build_inputs(submission, checks)
    try:
        return decide(*inputs)
    except Exception:
        # Never include raw inputs or exception messages in operational diagnostics.
        raise incompatible("calculation_error") from None


def result_snapshot(result) -> dict:
    """Every dataclass field, Decimal as a string, tuples as arrays; no float conversion."""
    return json.loads(json.dumps(asdict(result), default=str))


async def save_result(db, app, submission, result):
    if (result.outcome in {"APPROVED", "COUNTER_OFFER"}) != (result.offer is not None):
        raise incompatible("invalid_result")
    if result.policy_version != POLICY_VERSION:
        raise incompatible("incompatible_result_policy")
    data = result_snapshot(result)
    row = Decision(
        application_id=app.id,
        submission_id=submission.id,
        input_revision=submission.input_revision,
        revision=app.revision + 1,
        outcome=result.outcome,
        source="automatic",
        author_id=None,
        policy_version=result.policy_version,
        public_reasons=data["reasons"],
        public_explanation=result.explanation,
        required_documents=data["required_documents"],
        scoring_factors=data["scoring_factors"],
        analytical_explanation=result.analytical_explanation,
        risk_result=data,
    )
    db.add(row)
    await db.flush()
    if result.offer is not None:
        terms = result.offer
        if terms.product_code == submission.product_snapshot["code"]:
            # Preserve the submitted version even if the catalogue changed later.
            snapshot = submission.product_snapshot
            product_id = UUID(snapshot["id"])
        else:
            product = await db.scalar(
                select(Product).where(Product.code == terms.product_code).with_for_update(read=True)
            )
            if product is None or not product.active:
                raise incompatible("offered_product_unavailable")
            snapshot = {
                **public_product(product).model_dump(mode="json"),
                "version": product.version,
                "policy": product.policy_snapshot,
            }
            product_id = product.id
        compatible_product(snapshot)
        db.add(
            Offer(
                application_id=app.id,
                decision_id=row.id,
                product_id=product_id,
                product_snapshot=snapshot,
                amount=terms.amount,
                term_months=terms.term_months,
                annual_rate_percent=Decimal(terms.annual_rate_percent),
                expires_at=None,
            )
        )
    app.current_decision_id = row.id
    await db.flush()
    return row
