"""arq job queue helpers for enqueuing ingestion work from the API."""

import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

INGEST_JOB = "ingest_document"


async def create_arq_pool() -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


class IngestQueue(Protocol):
    async def enqueue_ingest(self, document_id: uuid.UUID) -> None: ...


class ArqIngestQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue_ingest(self, document_id: uuid.UUID) -> None:
        await self._pool.enqueue_job(INGEST_JOB, str(document_id))
