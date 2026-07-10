"""Seed a demo org by ingesting a corpus directory of documents.

Reusable by both the CLI seed script and tests. Each file becomes a Document
(source_type=text) whose bytes are written to storage, then run through the
real ingestion pipeline (extract -> chunk -> embed -> upsert).
"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Chunk, Document, DocumentSourceType, DocumentStatus, Organization
from app.services.embeddings import EmbeddingService
from app.services.ingestion.pipeline import run_ingestion
from app.services.storage import ContentStorage, document_key

DEMO_ORG_NAME = "Northwind Coffee Supply (Demo)"
CORPUS_GLOB = "*.md"


@dataclass
class SeedSummary:
    org_id: uuid.UUID
    document_count: int
    chunk_count: int


async def _reset_org(
    sessionmaker: async_sessionmaker[AsyncSession],
    name: str,
    public_key: str | None = None,
) -> uuid.UUID:
    async with sessionmaker() as session:
        existing = (
            (await session.execute(select(Organization).where(Organization.name == name)))
            .scalars()
            .all()
        )
        for org in existing:
            await session.delete(org)  # cascade removes documents + chunks
        await session.flush()
        org = Organization(name=name)
        if public_key is not None:
            org.public_key = public_key
        session.add(org)
        await session.commit()
        return org.id


async def seed_corpus(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService,
    storage: ContentStorage,
    *,
    corpus_dir: Path,
    org_name: str = DEMO_ORG_NAME,
    public_key: str | None = None,
) -> SeedSummary:
    files = await asyncio.to_thread(lambda: sorted(corpus_dir.glob(CORPUS_GLOB)))
    if not files:
        raise FileNotFoundError(f"no {CORPUS_GLOB} files found in {corpus_dir}")

    org_id = await _reset_org(sessionmaker, org_name, public_key)
    total_chunks = 0

    for path in files:
        async with sessionmaker() as session:
            document = Document(
                org_id=org_id,
                title=path.stem,
                source_type=DocumentSourceType.text,
                status=DocumentStatus.pending,
            )
            session.add(document)
            await session.flush()
            document_id = document.id
            await storage.put(document_key(str(document_id)), path.read_bytes())
            total_chunks += await run_ingestion(
                session,
                document_id,
                embedding_service=embedding_service,
                storage=storage,
            )

    return SeedSummary(org_id=org_id, document_count=len(files), chunk_count=total_chunks)


async def count_ready_documents(
    sessionmaker: async_sessionmaker[AsyncSession], org_id: uuid.UUID
) -> int:
    async with sessionmaker() as session:
        rows = await session.execute(
            select(Document.id).where(
                Document.org_id == org_id, Document.status == DocumentStatus.ready
            )
        )
        return len(rows.all())


async def count_chunks(sessionmaker: async_sessionmaker[AsyncSession], org_id: uuid.UUID) -> int:
    async with sessionmaker() as session:
        rows = await session.execute(select(Chunk.id).where(Chunk.org_id == org_id))
        return len(rows.all())
