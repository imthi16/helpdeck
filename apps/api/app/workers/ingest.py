"""arq job: ingest one document by id, scoped to its tenant."""

import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import async_session_factory, tenant_worker_session
from app.models import Document
from app.services.ingestion.pipeline import run_ingestion


async def _resolve_org_id(document_id: uuid.UUID) -> uuid.UUID | None:
    """Look up a document's org for jobs enqueued before org_id was added.

    Jobs already sitting in Redis at deploy time carry only ``document_id``;
    without this fallback they would TypeError and strand the document in
    ``pending``. Uses the owner engine because the tenant isn't known yet —
    a single primary-key read, and the actual ingestion still runs under the
    tenant-scoped app-role session below.
    """
    async with async_session_factory() as session:
        return await session.scalar(select(Document.org_id).where(Document.id == document_id))


async def ingest_document(ctx: dict[str, Any], document_id: str, org_id: str | None = None) -> int:
    doc_id = uuid.UUID(document_id)
    resolved_org = uuid.UUID(org_id) if org_id else await _resolve_org_id(doc_id)
    if resolved_org is None:
        # Document deleted between enqueue and execution; nothing to ingest.
        return 0
    # Session-scoped tenant setting: the pipeline manages its own commits
    # (status lifecycle), so a transaction-local setting would be lost after
    # the first one. RLS also fails closed if the document isn't the org's.
    async with tenant_worker_session(resolved_org, session_factory=ctx["sessionmaker"]) as session:
        return await run_ingestion(
            session,
            doc_id,
            embedding_service=ctx["embedding_service"],
            storage=ctx["storage"],
        )
