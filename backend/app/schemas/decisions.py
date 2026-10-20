from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import Months, PositiveMoney, Schema, Text, UTCDateTime


class Reason(Schema):
    code: Text = Field(description="Стабильный публичный код; словарь согласуется с рисками")
    message: Text


class RequiredDocument(Schema):
    code: Text
    title: Text
    explanation: Text


class OfferTerms(Schema):
    product_id: UUID
    amount: PositiveMoney
    term_months: Months
    annual_rate_percent: str | None = Field(
        default=None,
        pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$",
        description="Десятичная строка процентов годовых. null до согласования ставки; не рубли",
    )


class Offer(Schema):
    id: UUID
    terms: OfferTerms
    created_at: UTCDateTime
    expires_at: UTCDateTime | None = None


class DecisionBase(Schema):
    id: UUID
    created_at: UTCDateTime
    reasons: list[Reason]
    explanation: Text = Field(description="Клиентское объяснение без факторов и баллов скоринга")
    required_documents: list[RequiredDocument]


class ApprovedDecision(DecisionBase):
    outcome: Literal["APPROVED"]
    offer: Offer


class CounterOfferDecision(DecisionBase):
    outcome: Literal["COUNTER_OFFER"]
    offer: Offer


class NeedsInfoDecision(DecisionBase):
    outcome: Literal["NEEDS_INFO"]
    required_documents: list[RequiredDocument] = Field(min_length=1)


class ManualReviewDecision(DecisionBase):
    outcome: Literal["MANUAL_REVIEW"]


class RejectedDecision(DecisionBase):
    outcome: Literal["REJECTED"]
    reasons: list[Reason] = Field(min_length=1)


PublicDecision = Annotated[
    ApprovedDecision
    | CounterOfferDecision
    | NeedsInfoDecision
    | ManualReviewDecision
    | RejectedDecision,
    Field(discriminator="outcome"),
]
