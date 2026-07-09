"""Full ingestion pipeline: extract -> chunk -> embed -> upsert chunks.

Owns the ``documents.status`` lifecycle (pending -> processing -> ready|failed)
and records the error message on failure. Decoupled from arq: the worker job is
a thin wrapper that injects real dependencies; tests inject fakes.
"""

import uuid

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Chunk, Document, DocumentSourceType, DocumentStatus
from app.services.embeddings import EmbeddingService
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.extractors import (
    ExtractedDocument,
    extract_html,
    extract_pdf,
    extract_text,
)
from app.services.storage import ContentStorage, StorageError, document_key

logger = get_logger(__name__)

MAX_ERROR_LENGTH = 2000


class IngestionError(Exception):
    pass


async def _load_source(
    document_id: uuid.UUID,
    source_type: DocumentSourceType,
    source_url: str | None,
    storage: ContentStorage,
    http_client: httpx.AsyncClient,
) -> ExtractedDocument:
    key = document_key(str(document_id))

    if source_type == DocumentSourceType.pdf:
        return extract_pdf(await storage.get(key))

    if source_type == DocumentSourceType.text:
        return extract_text((await storage.get(key)).decode("utf-8"))

    if source_type == DocumentSourceType.url:
        if not source_url:
            raise IngestionError("url document has no source_url")
        response = await http_client.get(source_url, follow_redirects=True)
        response.raise_for_status()
        return extract_html(response.text, url=source_url)

    raise IngestionError(f"unsupported source_type: {source_type}")


async def run_ingestion(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    embedding_service: EmbeddingService,
    storage: ContentStorage,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    """Run ingestion for one document. Returns the number of chunks persisted."""
    document = await session.get(Document, document_id)
    if document is None:
        raise IngestionError(f"document {document_id} not found")

    # Capture fields before the first commit; expired ORM reads afterward would
    # do synchronous IO and fail outside a greenlet.
    org_id = document.org_id
    title = document.title
    source_type = document.source_type
    source_url = document.source_url

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        document.status = DocumentStatus.processing
        document.error = None
        await session.commit()

        extracted = await _load_source(document_id, source_type, source_url, storage, client)
        chunks = chunk_text(
            extracted.text,
            base_metadata={
                "document_title": title,
                "source_type": str(source_type),
                **extracted.metadata,
            },
        )
        vectors = await embedding_service.embed_texts([chunk.content for chunk in chunks])

        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                Chunk(
                    org_id=org_id,
                    document_id=document_id,
                    content=chunk.content,
                    embedding=vector,
                    meta=chunk.metadata,
                    token_count=chunk.token_count,
                )
            )

        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.ready, error=None)
        )
        await session.commit()
        logger.info("ingestion_complete", document_id=str(document_id), chunks=len(chunks))
        return len(chunks)
    except Exception as exc:
        await session.rollback()
        await _mark_failed(session, document_id, exc)
        raise
    finally:
        if owns_client:
            await client.aclose()


async def _mark_failed(session: AsyncSession, document_id: uuid.UUID, exc: Exception) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        return
    document.status = DocumentStatus.failed
    document.error = str(exc)[:MAX_ERROR_LENGTH]
    await session.commit()
    logger.warning("ingestion_failed", document_id=str(document_id), error=str(exc))


async def count_chunks(session: AsyncSession, document_id: uuid.UUID) -> int:
    result = await session.execute(select(Chunk.id).where(Chunk.document_id == document_id))
    return len(result.all())


__all__ = ["IngestionError", "StorageError", "count_chunks", "run_ingestion"]
