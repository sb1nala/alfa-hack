from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.applications import ClientApplication
from app.schemas.common import (
    ApplicationStatus,
    Revision,
    RevisionRequest,
    Schema,
    Text,
    UTCDateTime,
)
from app.schemas.decisions import OfferTerms, PublicDecision, Reason, RequiredDocument


class ScoringFactor(Schema):
    code: Text
    value: Text
    contribution: str | None = Field(
        default=None, description="Внутренний вклад; шкала принадлежит рискам"
    )
    explanation: Text


class BankDecision(Schema):
    public: PublicDecision
    source: Literal["automatic", "underwriter"]
    author_id: UUID | None
    policy_version: str | None
    scoring_factors: list[ScoringFactor]
    analytical_explanation: Text
    review_reason: str | None = Field(
        description=(
            "Только банку: причина MANUAL_REVIEW из сохранённого результата этого решения. "
            "Известные коды: gray_zone, collateral_review, documents_review, "
            "revenue_mismatch_review, incomplete_checks. Неизвестный непустой строковый код "
            "передаётся как есть; используйте analytical_explanation как fallback. "
            "Для остальных исходов, отсутствующего или некорректного значения — null."
        )
    )


class UnderwriterApplication(Schema):
    application: ClientApplication
    decisions: list[BankDecision] = Field(description="История решений, от старого к новому")
    allowed_actions: list[Literal["decide"]]


class UnderwriterQueueItem(Schema):
    id: UUID
    owner_id: UUID
    company_name: str | None
    status: ApplicationStatus
    revision: Revision
    updated_at: UTCDateTime


class ManualDecisionBase(RevisionRequest):
    reasons: list[Reason]
    explanation: Text = Field(description="Будет виден клиенту")
    analytical_explanation: Text = Field(description="Только банку")


class ManualApprovalRequest(ManualDecisionBase):
    outcome: Literal["APPROVED"]
    offer_terms: OfferTerms


class ManualCounterOfferRequest(ManualDecisionBase):
    outcome: Literal["COUNTER_OFFER"]
    offer_terms: OfferTerms


class ManualNeedsInfoRequest(ManualDecisionBase):
    outcome: Literal["NEEDS_INFO"]
    required_documents: list[RequiredDocument] = Field(min_length=1)


class ManualRejectionRequest(ManualDecisionBase):
    outcome: Literal["REJECTED"]
    reasons: list[Reason] = Field(min_length=1)


ManualDecisionRequest = Annotated[
    ManualApprovalRequest
    | ManualCounterOfferRequest
    | ManualNeedsInfoRequest
    | ManualRejectionRequest,
    Field(discriminator="outcome"),
]
