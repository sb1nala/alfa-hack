from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Path, Response, UploadFile

from app.api import examples as ex
from app.api.applications import ApplicationID
from app.api.contract import endpoint, not_implemented
from app.schemas.applications import Document, DocumentList

router = APIRouter(prefix="/api/applications/{application_id}/documents", tags=["documents"])
upload_contract = endpoint(
    Document,
    {"document": ex.DOCUMENT},
    "Загрузить документ только в DRAFT/NEEDS_INFO. multipart/form-data: file, document_type, "
    "expected_revision. Загружает только владелец client. Проверки размера/MIME и хранение "
    "реализуются позднее; лимиты согласовать с фронтендом. Ошибки 413/415 зарезервированы. "
    "Загрузка повышает revision заявки; перечитать карточку перед следующей мутацией.",
    mutation=True,
    status_code=201,
    extra_errors=(413, 415),
)
upload_contract["openapi_extra"]["requestBody"] = {
    "content": {
        "multipart/form-data": {
            "examples": ex.examples(
                {
                    "upload": {
                        "file": "demo",
                        "document_type": "DEMO_DOCUMENT",
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
) -> Document:
    not_implemented()


@router.get(
    "",
    **endpoint(
        DocumentList,
        {"documents": {"items": [ex.DOCUMENT]}},
        "Метаданные документов заявки: владелец client или underwriter. Пути "
        "хранилища не раскрываются.",
        role="both",
    ),
)
async def list_documents(application_id: ApplicationID) -> DocumentList:
    not_implemented()


download_contract = endpoint(
    None,
    {},
    "Скачать документ как attachment: владелец client или underwriter. "
    "Будущий ответ — бинарное тело; Content-Disposition содержит безопасное имя файла.",
    role="both",
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
) -> Response:
    not_implemented()
