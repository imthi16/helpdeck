"""Documents CRUD + reindex. Uploads store raw bytes and enqueue ingestion."""

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory, tenant_session
from app.core.deps import MembershipDep, require_role
from app.models import Chunk, Document, DocumentSourceType, DocumentStatus, MembershipRole
from app.schemas.document import DocumentCreate, DocumentResponse
from app.services.queue import ArqIngestQueue, IngestQueue
from app.services.storage import ContentStorage, document_key, get_storage

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def get_documents_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # Base factory for the tenant lane; every endpoint below opens
    # tenant_session(org_id, session_factory=<this>) so RLS is enforced.
    return app_session_factory


def get_documents_storage() -> ContentStorage:
    return get_storage()


def get_ingest_queue(request: Request) -> IngestQueue:
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingestion queue unavailable",
        )
    return ArqIngestQueue(pool)


def current_org_id(membership: MembershipDep) -> uuid.UUID:
    return membership.org_id


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_documents_sessionmaker)]
StorageDep = Annotated[ContentStorage, Depends(get_documents_storage)]
QueueDep = Annotated[IngestQueue, Depends(get_ingest_queue)]
OrgDep = Annotated[uuid.UUID, Depends(current_org_id)]
# KB mutations are admin+; reads are any member (RBAC matrix, task 5.2).
admin_required = Depends(require_role(MembershipRole.admin))


def _to_response(document: Document, chunk_count: int) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        status=document.status,
        error=document.error,
        chunk_count=chunk_count,
        created_at=document.created_at,
    )


async def _get_owned_document(
    session: AsyncSession, document_id: uuid.UUID, org_id: uuid.UUID
) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.get("", response_model=list[DocumentResponse])
async def list_documents(sessionmaker: SessionmakerDep, org_id: OrgDep) -> list[DocumentResponse]:
    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        rows = (
            await session.execute(
                select(Document, func.count(Chunk.id))
                .outerjoin(Chunk, Chunk.document_id == Document.id)
                .where(Document.org_id == org_id)
                .group_by(Document.id)
                .order_by(Document.created_at.desc())
            )
        ).all()
    return [_to_response(document, count) for document, count in rows]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID, sessionmaker: SessionmakerDep, org_id: OrgDep
) -> DocumentResponse:
    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = await _get_owned_document(session, document_id, org_id)
        count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        )
    return _to_response(document, count or 0)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[admin_required],
)
async def upload_document(
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    queue: QueueDep,
    org_id: OrgDep,
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    filename = file.filename or "document.pdf"
    if not (filename.lower().endswith(".pdf") or file.content_type == "application/pdf"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="only PDF uploads"
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file too large"
        )

    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = Document(
            org_id=org_id,
            title=filename.rsplit(".", 1)[0],
            source_type=DocumentSourceType.pdf,
            status=DocumentStatus.pending,
        )
        session.add(document)
        await session.flush()  # populate the UUID default before leaving the block
        document_id = document.id

    await storage.put(document_key(str(document_id)), data)
    await queue.enqueue_ingest(document_id, org_id)

    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = await session.get(Document, document_id)
        return _to_response(document, 0)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[admin_required],
)
async def create_document(
    payload: DocumentCreate,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    queue: QueueDep,
    org_id: OrgDep,
) -> DocumentResponse:
    title = payload.title or (payload.url if payload.source_type == DocumentSourceType.url else "")
    if not title:
        title = "Untitled"

    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = Document(
            org_id=org_id,
            title=title[:512],
            source_type=payload.source_type,
            source_url=payload.url,
            status=DocumentStatus.pending,
        )
        session.add(document)
        await session.flush()  # populate the UUID default before leaving the block
        document_id = document.id

    if payload.source_type == DocumentSourceType.text and payload.content:
        await storage.put(document_key(str(document_id)), payload.content.encode("utf-8"))
    await queue.enqueue_ingest(document_id, org_id)

    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = await session.get(Document, document_id)
        return _to_response(document, 0)


@router.post(
    "/{document_id}/reindex", response_model=DocumentResponse, dependencies=[admin_required]
)
async def reindex_document(
    document_id: uuid.UUID,
    sessionmaker: SessionmakerDep,
    queue: QueueDep,
    org_id: OrgDep,
) -> DocumentResponse:
    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = await _get_owned_document(session, document_id, org_id)
        document.status = DocumentStatus.pending
        document.error = None
    await queue.enqueue_ingest(document_id, org_id)
    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        document = await session.get(Document, document_id)
        count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        )
    return _to_response(document, count or 0)


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[admin_required]
)
async def delete_document(
    document_id: uuid.UUID,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    org_id: OrgDep,
) -> None:
    async with tenant_session(org_id, session_factory=sessionmaker) as session:
        await _get_owned_document(session, document_id, org_id)  # 404s if not owned
        await session.execute(delete(Document).where(Document.id == document_id))
    await storage.delete(document_key(str(document_id)))
