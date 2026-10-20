"""Wire types only; no persistence, credit policy or authentication logic."""

from datetime import timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


def require_utc(value: AwareDatetime) -> AwareDatetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must use UTC")
    return value


UTCDateTime = Annotated[
    AwareDatetime,
    AfterValidator(require_utc),
    Field(description="ISO 8601 in UTC", json_schema_extra={"pattern": r".*(Z|\+00:00)$"}),
]
Money = Annotated[
    str,
    Field(
        pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$",
        max_length=30,
        description="Рубли, строка с 2 знаками",
    ),
]
PositiveMoney = Annotated[
    str,
    Field(
        pattern=r"^([1-9][0-9]*\.[0-9]{2}|0\.(0[1-9]|[1-9][0-9]))$",
        max_length=30,
        description="Положительная сумма в рублях, строка с 2 знаками",
    ),
]
INN = Annotated[
    str,
    Field(
        pattern=r"^([0-9]{10}|[0-9]{12})$", description="Формат ИНН; без проверки контрольной суммы"
    ),
]
Revision = Annotated[int, Field(ge=1, strict=True)]
Months = Annotated[int, Field(ge=1, strict=True, description="Срок в целых месяцах")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Role(StrEnum):
    CLIENT = "client"
    UNDERWRITER = "underwriter"


class ApplicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CHECKING = "CHECKING"
    SCORING = "SCORING"
    NEEDS_INFO = "NEEDS_INFO"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    APPROVED = "APPROVED"
    COUNTER_OFFER = "COUNTER_OFFER"
    REJECTED = "REJECTED"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    SIGNED = "SIGNED"
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class AllowedAction(StrEnum):
    EDIT = "edit"
    SUBMIT = "submit"
    CANCEL = "cancel"
    ACCEPT_OFFER = "accept_offer"
    PROVIDE_INFO = "provide_info"
    UPLOAD_DOCUMENT = "upload_document"
    SIGN = "sign"


class FieldError(Schema):
    field: str = Field(description="Путь, например body.amount или path.application_id")
    code: str
    message: str


class ErrorResponse(Schema):
    code: str
    message: str
    field_errors: list[FieldError]
    request_id: UUID


class RevisionRequest(Schema):
    expected_revision: Revision


class PageQuery(Schema):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class Page[T](Schema):
    items: list[T]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
