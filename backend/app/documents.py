"""B11 document storage: content-sniffed uploads, opaque keys, no paths on the wire."""

import hashlib
import os
import unicodedata
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.decision.texts import REQUIRED_DOCUMENTS
from app.dependencies import Principal
from app.models import Application, Document
from app.schemas.applications import Document as DocumentSchema
from app.transitions import append_event, lock_application

# annual_revenue is a form field, not a file.
DOCUMENT_TYPES = frozenset(REQUIRED_DOCUMENTS) - {"annual_revenue"}
UPLOAD_STATUSES = frozenset({"DRAFT", "NEEDS_INFO"})
# The stored media type comes from the bytes; the browser's claim must agree.
SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}
CHUNK = 1024 * 1024


def public(document: Document) -> DocumentSchema:
    return DocumentSchema(
        id=document.id,
        application_id=document.application_id,
        uploaded_by=document.uploaded_by,
        document_type=document.document_type,
        filename=document.filename,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        created_at=document.created_at,
    )


def safe_filename(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(
        c for c in unicodedata.normalize("NFC", base) if unicodedata.category(c)[0] != "C"
    )
    base = base.strip().strip(".")[:200]
    return base or "document"


def content_disposition(filename: str) -> str:
    fallback = "".join(c if c.isascii() and c.isalnum() or c in "._-" else "_" for c in filename)
    return f"attachment; filename=\"{fallback or 'document'}\"; filename*=UTF-8''{quote(filename)}"


def storage_path(settings: Settings, key: str) -> Path:
    if len(key) != 32 or not all(c in "0123456789abcdef" for c in key):
        raise ValueError("invalid storage key")
    return settings.documents_dir / key[:2] / key


async def read_limited(file: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while chunk := await file.read(CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413)
        chunks.append(chunk)
    return b"".join(chunks)


def sniff(content: bytes) -> str:
    for signature, media_type in SIGNATURES.items():
        if content.startswith(signature):
            return media_type
    raise HTTPException(415)


def write_file(settings: Settings, key: str, content: bytes) -> None:
    target = storage_path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    with open(partial, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, target)


def read_file(settings: Settings, storage_key: str, size_bytes: int, sha256: str) -> bytes:
    try:
        content = storage_path(settings, storage_key).read_bytes()
    except (OSError, ValueError):
        raise HTTPException(404) from None
    if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != sha256:
        raise HTTPException(404)
    return content


async def upload(
    db: AsyncSession,
    settings: Settings,
    application_id: UUID,
    *,
    file: UploadFile,
    document_type: str,
    expected_revision: int,
    principal: Principal,
    request_id: UUID,
) -> DocumentSchema:
    if document_type not in DOCUMENT_TYPES:
        raise RequestValidationError(
            [{"loc": ("body", "document_type"), "type": "unknown_document_type"}]
        )
    # Validate the body before taking the application lock.
    content = await read_limited(file, settings.document_max_bytes)
    if not content:
        raise RequestValidationError([{"loc": ("body", "file"), "type": "empty_file"}])
    media_type = sniff(content)
    if file.content_type not in {None, "application/octet-stream", media_type}:
        raise HTTPException(415)
    application = await lock_application(db, application_id, owner_id=principal.user.id)
    if application.status not in UPLOAD_STATUSES or application.revision != expected_revision:
        raise HTTPException(409)
    application.revision += 1
    application.updated_at = await db.scalar(select(func.clock_timestamp()))
    key = uuid4().hex
    document = Document(
        application_id=application.id,
        uploaded_by=principal.user.id,
        application_revision=application.revision,
        document_type=document_type,
        filename=safe_filename(file.filename),
        media_type=media_type,
        size_bytes=len(content),
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=application.updated_at,
    )
    db.add(document)
    await append_event(
        db,
        application,
        actor_kind="client",
        actor_user_id=principal.user.id,
        request_id=request_id,
        event_type="document_uploaded",
        previous_status=application.status,
        public_message="Документ загружен",
        audit_payload={"document_type": document_type, "size_bytes": len(content)},
    )
    await db.flush()
    # The file lands before commit: a failed commit leaves an unreferenced blob, never a
    # row without content. Blobs are content-addressed only by their random key.
    # Starlette uses AnyIO's non-abandoning worker: scope cancellation waits for I/O.
    # Only plain file inputs enter the worker; the session stays on the event loop.
    await run_in_threadpool(write_file, settings, key, content)
    return public(document)


async def listing(db: AsyncSession, application: Application) -> list[DocumentSchema]:
    rows = await db.scalars(
        select(Document)
        .where(Document.application_id == application.id)
        .order_by(Document.created_at, Document.id)
    )
    return [public(row) for row in rows]


async def types(db: AsyncSession, application: Application) -> list[str]:
    """Sorted distinct document types; caller holds the application lock."""
    rows = await db.scalars(
        select(Document.document_type)
        .where(Document.application_id == application.id)
        .distinct()
        .order_by(Document.document_type)
    )
    return list(rows)
