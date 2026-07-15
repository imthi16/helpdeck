"""arq job: ingest one document by id, scoped to its tenant."""

import uuid
from typing import Any

from app.core.db import tenant_worker_session
from app.services.ingestion.pipeline import run_ingestion


async def ingest_document(ctx: dict[str, Any], document_id: str, org_id: str) -> int:
    # Session-scoped tenant setting: the pipeline manages its own commits
    # (status lifecycle), so a transaction-local setting would be lost after
    # the first one. RLS also fails closed if the document isn't the org's.
    async with tenant_worker_session(
        uuid.UUID(org_id), session_factory=ctx["sessionmaker"]
    ) as session:
        return await run_ingestion(
            session,
            uuid.UUID(document_id),
            embedding_service=ctx["embedding_service"],
            storage=ctx["storage"],
        )
