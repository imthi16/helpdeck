"""Public demo org support (task 7.3).

The demo org (``DEMO_ORG_ID`` setting) is a read-only showcase: its knowledge
base, members, keys, and settings cannot be mutated through the dashboard —
chat, thumbs, and CSAT stay open because they *are* the demo. A nightly arq
cron wipes its conversations and re-asserts the seeded corpus so every
visitor gets a fresh instance.
"""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.deps import MembershipDep
from app.core.logging import get_logger
from app.models import Conversation

logger = get_logger(__name__)


def is_demo_org(org_id: uuid.UUID) -> bool:
    demo = get_settings().demo_org_id
    return bool(demo) and str(org_id) == demo


async def block_demo_writes(membership: MembershipDep) -> None:
    """Route dependency: 403 mutations against the demo organization."""
    if is_demo_org(membership.org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="the demo organization is read-only",
        )


async def reset_demo_conversations(
    sessionmaker: async_sessionmaker[AsyncSession], org_id: uuid.UUID
) -> int:
    """Delete the demo org's conversations (cascades to messages/escalations)."""
    async with sessionmaker() as session:
        result = await session.execute(delete(Conversation).where(Conversation.org_id == org_id))
        await session.commit()
        return result.rowcount or 0


async def reset_demo_org(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly arq cron: wipe demo conversations, re-seed the corpus.

    No-ops when DEMO_ORG_ID is unset. Runs on the superuser engine (seeding
    creates/deletes the org itself), like scripts/seed.py.
    """
    settings = get_settings()
    if not settings.demo_org_id:
        return {"skipped": "demo_org_id unset"}

    from app.core.db import async_session_factory
    from app.services.embeddings import EmbeddingService
    from app.services.ingestion.seed import find_corpus_dir, ingest_corpus_into
    from app.services.storage import get_storage

    org_id = uuid.UUID(settings.demo_org_id)
    removed = await reset_demo_conversations(async_session_factory, org_id)
    # Re-ingest into the SAME org row: DEMO_ORG_ID must stay valid, so the
    # org is never deleted — only its documents/chunks are replaced.
    summary = await ingest_corpus_into(
        async_session_factory,
        EmbeddingService(),
        get_storage(),
        corpus_dir=find_corpus_dir(),
        org_id=org_id,
        replace=True,
    )
    logger.info(
        "demo_org_reset",
        conversations_removed=removed,
        documents=summary.document_count,
        chunks=summary.chunk_count,
    )
    return {"conversations_removed": removed, "documents": summary.document_count}
