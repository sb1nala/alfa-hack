from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.catalog import CompanyInput
from app.schemas.common import (
    AllowedAction,
    ApplicationStatus,
    Money,
    Months,
    PositiveMoney,
    Revision,
    RevisionRequest,
    Schema,
    Text,
    UTCDateTime,
)
from app.schemas.decisions import PublicDecision


class DraftFields(Schema):
    company: CompanyInput | None = None
    product_id: UUID | None = None
    amount: PositiveMoney | None = None
    term_months: Months | None = None
    purpose: Text | None = None
    annual_revenue: Money | None = None


class CreateDraftRequest(DraftFields):
    pass


class EditDraftRequest(DraftFields, RevisionRequest):
    """Only sent fields change; null clears a field. DRAFT only."""


class SubmitRequest(RevisionRequest):
    consent_to_checks: Literal[True]


class CancelRequest(RevisionRequest):
    reason: Text | None = None


class AcceptOfferRequest(RevisionRequest):
    offer_id: UUID


class AdditionalInfoRequest(RevisionRequest):
    comment: Text
    document_ids: list[UUID] = Field(default_factory=list)
    annual_revenue: Money | None = None


class SignRequest(RevisionRequest):
    offer_id: UUID
    consent: Literal[True]


class ClientApplicationSummary(Schema):
    id: UUID
    company_name: str | None
    product_id: UUID | None
    amount: PositiveMoney | None
    term_months: Months | None
    status: ApplicationStatus
    revision: Revision
    allowed_actions: list[AllowedAction]
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ClientApplication(ClientApplicationSummary):
    owner_id: UUID
    form: DraftFields
    decision: PublicDecision | None = Field(description="Последнее публичное решение либо null")
    accepted_offer_id: UUID | None


class Document(Schema):
    id: UUID
    application_id: UUID
    uploaded_by: UUID
    document_type: Text
    filename: Text
    media_type: Text
    size_bytes: int = Field(ge=0)
    created_at: UTCDateTime


class DocumentList(Schema):
    items: list[Document]


class ApplicationEvent(Schema):
    id: UUID = Field(description="Непрозрачный курсор SSE, не порядковый номер")
    application_id: UUID
    revision: Revision
    event_type: Literal[
        "status_changed", "application_updated", "decision_created", "document_uploaded"
    ]
    status: ApplicationStatus
    previous_status: ApplicationStatus | None
    occurred_at: UTCDateTime
    message: Text = Field(
        description="Только публичный текст, без внутренних факторов и снимков моков"
    )


class EventList(Schema):
    items: list[ApplicationEvent]
    next_cursor: UUID | None = Field(
        description="Для следующей страницы истории; null, если история прочитана"
    )


class StatusChangedData(Schema):
    event_type: Literal["status_changed"]
    application_id: UUID
    revision: Revision
    previous_status: ApplicationStatus
    status: ApplicationStatus
    occurred_at: UTCDateTime


class ApplicationUpdatedData(Schema):
    event_type: Literal["application_updated", "decision_created", "document_uploaded"]
    application_id: UUID
    revision: Revision
    status: ApplicationStatus
    occurred_at: UTCDateTime


SSEData = Annotated[StatusChangedData | ApplicationUpdatedData, Field(discriminator="event_type")]


class SSEEvent(Schema):
    """OpenAPI describes the frame envelope; the wire response is text/event-stream."""

    id: UUID
    event: Literal["status_changed", "application_updated", "decision_created", "document_uploaded"]
    data: SSEData
