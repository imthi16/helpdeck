import uuid
from pathlib import Path
from typing import Any

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import (
    Chunk,
    Document,
    DocumentSourceType,
    DocumentStatus,
    Organization,
)
from app.services.embeddings import EMBEDDING_DIMS as EMBED_DIMS
from app.services.embeddings import EmbeddingService
from app.services.ingestion.pipeline import run_ingestion
from app.services.storage import LocalFileStorage, document_key
from app.workers.ingest import ingest_document

FIXTURES = Path(__file__).parent / "fixtures"

Sessionmaker = async_sessionmaker[AsyncSession]


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.calls.append(list(texts))
        # Deterministic non-zero vectors of the correct dimensionality.
        return [[float((i % 7) + 1) / 10.0] * EMBED_DIMS for i in range(len(texts))]


def fake_embedding_service() -> EmbeddingService:
    return EmbeddingService(FakeEmbeddingProvider(), model="test-embed")


async def _create_document(
    sessionmaker: Sessionmaker,
    *,
    source_type: DocumentSourceType,
    title: str = "Fixture Doc",
    source_url: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with sessionmaker() as session:
        org = Organization(name=f"org-{uuid.uuid4()}")
        session.add(org)
        await session.flush()
        document = Document(
            org_id=org.id,
            title=title,
            source_type=source_type,
            source_url=source_url,
            status=DocumentStatus.pending,
        )
        session.add(document)
        await session.commit()
        return org.id, document.id


async def _delete_org(sessionmaker: Sessionmaker, org_id: uuid.UUID) -> None:
    async with sessionmaker() as session:
        org = await session.get(Organization, org_id)
        if org is not None:
            await session.delete(org)  # cascades to documents + chunks
            await session.commit()


async def test_ingest_pdf_job_reaches_ready_with_embeddings(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    org_id, doc_id = await _create_document(db_sessionmaker, source_type=DocumentSourceType.pdf)
    storage = LocalFileStorage(tmp_path)
    await storage.put(document_key(str(doc_id)), (FIXTURES / "sample.pdf").read_bytes())

    try:
        ctx = {
            "sessionmaker": db_sessionmaker,
            "embedding_service": fake_embedding_service(),
            "storage": storage,
        }
        n_chunks = await ingest_document(ctx, str(doc_id), str(org_id))
        assert n_chunks > 0

        async with db_sessionmaker() as session:
            document = await session.get(Document, doc_id)
            assert document is not None
            assert document.status == DocumentStatus.ready
            assert document.error is None

            chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_id == doc_id)))
                .scalars()
                .all()
            )
            assert len(chunks) == n_chunks
            for chunk in chunks:
                assert chunk.embedding is not None
                assert len(chunk.embedding) == EMBED_DIMS
                assert chunk.org_id == org_id
                assert chunk.token_count > 0
                assert chunk.meta["document_title"] == "Fixture Doc"
    finally:
        await _delete_org(db_sessionmaker, org_id)


async def test_ingest_text_job_persists_chunks(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    org_id, doc_id = await _create_document(db_sessionmaker, source_type=DocumentSourceType.text)
    storage = LocalFileStorage(tmp_path)
    await storage.put(document_key(str(doc_id)), (FIXTURES / "sample.md").read_bytes())

    try:
        ctx = {
            "sessionmaker": db_sessionmaker,
            "embedding_service": fake_embedding_service(),
            "storage": storage,
        }
        n_chunks = await ingest_document(ctx, str(doc_id), str(org_id))
        assert n_chunks > 0

        async with db_sessionmaker() as session:
            document = await session.get(Document, doc_id)
            assert document is not None
            assert document.status == DocumentStatus.ready
    finally:
        await _delete_org(db_sessionmaker, org_id)


async def test_failed_extraction_records_error(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    org_id, doc_id = await _create_document(db_sessionmaker, source_type=DocumentSourceType.pdf)
    storage = LocalFileStorage(tmp_path)
    await storage.put(document_key(str(doc_id)), b"this is not a valid pdf")

    try:
        async with db_sessionmaker() as session:
            with pytest.raises(Exception):  # noqa: B017 - any extraction failure
                await run_ingestion(
                    session,
                    doc_id,
                    embedding_service=fake_embedding_service(),
                    storage=storage,
                )

        async with db_sessionmaker() as session:
            document = await session.get(Document, doc_id)
            assert document is not None
            assert document.status == DocumentStatus.failed
            assert document.error
    finally:
        await _delete_org(db_sessionmaker, org_id)


async def test_enqueue_and_worker_burst_run(db_sessionmaker: Sessionmaker, tmp_path: Path) -> None:
    """True arq round-trip: enqueue to Redis, run the worker in burst mode."""
    org_id, doc_id = await _create_document(db_sessionmaker, source_type=DocumentSourceType.text)
    storage = LocalFileStorage(tmp_path)
    await storage.put(document_key(str(doc_id)), (FIXTURES / "sample.md").read_bytes())

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = f"test:ingest:{uuid.uuid4()}"

    async def startup(ctx: dict[str, Any]) -> None:
        ctx["sessionmaker"] = db_sessionmaker
        ctx["embedding_service"] = fake_embedding_service()
        ctx["storage"] = storage

    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("ingest_document", str(doc_id), str(org_id), _queue_name=queue_name)
        worker = Worker(
            functions=[ingest_document],
            redis_settings=redis_settings,
            queue_name=queue_name,
            burst=True,
            poll_delay=0.1,
            on_startup=startup,
        )
        try:
            await worker.async_run()
        finally:
            await worker.close()

        async with db_sessionmaker() as session:
            document = await session.get(Document, doc_id)
            assert document is not None
            assert document.status == DocumentStatus.ready
            count = (
                await session.execute(select(Chunk.id).where(Chunk.document_id == doc_id))
            ).all()
            assert len(count) > 0
    finally:
        await pool.aclose()
        await _delete_org(db_sessionmaker, org_id)


async def test_reindex_replaces_existing_chunks(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    org_id, doc_id = await _create_document(db_sessionmaker, source_type=DocumentSourceType.text)
    storage = LocalFileStorage(tmp_path)
    await storage.put(document_key(str(doc_id)), (FIXTURES / "sample.md").read_bytes())
    ctx = {
        "sessionmaker": db_sessionmaker,
        "embedding_service": fake_embedding_service(),
        "storage": storage,
    }

    try:
        first = await ingest_document(ctx, str(doc_id), str(org_id))
        second = await ingest_document(ctx, str(doc_id), str(org_id))
        assert first == second

        async with db_sessionmaker() as session:
            chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_id == doc_id)))
                .scalars()
                .all()
            )
            # Re-index must not duplicate chunks.
            assert len(chunks) == second
    finally:
        await _delete_org(db_sessionmaker, org_id)
