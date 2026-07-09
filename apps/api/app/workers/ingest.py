"""arq job: ingest one document by id."""

import uuid
from typing import Any

from app.services.ingestion.pipeline import run_ingestion


async def ingest_document(ctx: dict[str, Any], document_id: str) -> int:
    sessionmaker = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await run_ingestion(
            session,
            uuid.UUID(document_id),
            embedding_service=ctx["embedding_service"],
            storage=ctx["storage"],
        )
