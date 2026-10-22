from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Request, Response, UploadFile

from app import documents
from app.api import examples as ex
from app.api.applications import ApplicationID
from app.api.contract import endpoint
from app.dependencies import (
    ClientPrincipal,
    DBSession,
    accessible_application,
    application_document,
    owned_application,
)
from app.models import Application
from app.models import Document as DocumentRow
from app.schemas.applications import Document, DocumentList

router = APIRouter(prefix="/api/applications/{application_id}/documents", tags=["documents"])
upload_contract = endpoint(
    Document,
    {"document": ex.DOCUMENT},
    "Загрузить документ только в DRAFT/NEEDS_INFO. multipart/form-data: file, document_type, "
    "expected_revision. Загружает только владелец client. document_type — код из "
    "required_documents (bank_statement_12m, financial_statements, tax_return, "
    "lease_agreement, equipment_invoice, debt_details), иначе 422. Файл — PDF, JPEG или PNG "
    "(тип определяется по содержимому и должен совпасть с заявленным, иначе 415), "
    "не больше 10 МБ (иначе 413), пустой файл — 422. "
    "Загрузка повышает revision заявки; перечитать карточку перед следующей мутацией.",
    mutation=True,
    status_code=201,
    extra_errors=(413, 415),
    access=owned_application,
    implemented=True,
)
upload_contract["openapi_extra"]["requestBody"] = {
    "content": {
        "multipart/form-data": {
            "examples": ex.examples(
                {
                    "upload": {
                        "file": "demo",
                        "document_type": "bank_statement_12m",
                        "expected_revision": 1,
                    }
                }
            ),
        }
    }
}


@router.post("", **upload_contract)
async def upload_document(
    application_id: ApplicationID,
    file: Annotated[UploadFile, File(description="Бинарное содержимое документа")],
    document_type: Annotated[str, Form(min_length=1, max_length=4000)],
    expected_revision: Annotated[int, Form(ge=1)],
    db: DBSession,
    principal: ClientPrincipal,
    request: Request,
) -> Document:
    result = await documents.upload(
        db,
        request.app.state.settings,
        application_id,
        file=file,
        document_type=document_type,
        expected_revision=expected_revision,
        principal=principal,
        request_id=request.state.request_id,
    )
    await db.commit()
    return result


@router.get(
    "",
    **endpoint(
        DocumentList,
        {"documents": {"items": [ex.DOCUMENT]}},
        "Метаданные документов заявки: владелец client или underwriter. Пути "
        "хранилища не раскрываются.",
        role="both",
        access=accessible_application,
        implemented=True,
    ),
)
async def list_documents(
    application_id: ApplicationID,
    db: DBSession,
    application: Annotated[Application, Depends(accessible_application)],
) -> DocumentList:
    return DocumentList(items=await documents.listing(db, application))


download_contract = endpoint(
    None,
    {},
    "Скачать документ как attachment: владелец client или underwriter. "
    "Бинарное тело с сохранённым media_type; Content-Disposition содержит безопасное имя "
    "файла, X-Content-Type-Options: nosniff.",
    role="both",
    access=application_document,
    implemented=True,
    headers={
        "Content-Disposition": {
            "schema": {"type": "string"},
            "description": "attachment; filename=demo.txt",
        }
    },
)
download_contract["responses"][200]["content"] = {
    "application/octet-stream": {
        "schema": {"type": "string", "format": "binary"},
        "examples": ex.examples({"file": "demo"}),
    },
}


@router.get("/{document_id}/download", response_class=Response, **download_contract)
async def download_document(
    application_id: ApplicationID,
    document_id: Annotated[UUID, Path(examples=[ex.DOCUMENT_ID])],
    document: Annotated[DocumentRow, Depends(application_document)],
    request: Request,
) -> Response:
    return Response(
        documents.read_file(request.app.state.settings, document),
        media_type=document.media_type,
        headers={
            "Content-Disposition": documents.content_disposition(document.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )
