"""Demo mode (task 7.3): the demo org is read-only; reset job re-ingests."""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app
from app.models import Conversation, ConversationChannel, Document, Membership, MembershipRole, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.documents import get_documents_sessionmaker
from app.services import demo
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def demo_org(db_sessionmaker: Sessionmaker, tmp_path, monkeypatch: pytest.MonkeyPatch):
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"demo-{uuid.uuid4().hex[:8]}",
    )
    marker = uuid.uuid4().hex[:8]
    async with db_sessionmaker() as session:
        owner = User(email=f"demo-{marker}@example.com", password_hash="x", name="D")
        session.add(owner)
        await session.flush()
        session.add(Membership(org_id=summary.org_id, user_id=owner.id, role=MembershipRole.owner))
        session.add(Conversation(org_id=summary.org_id, channel=ConversationChannel.widget))
        await session.commit()
        owner_id = owner.id

    monkeypatch.setattr(get_settings(), "demo_org_id", str(summary.org_id), raising=False)
    headers = {"Authorization": f"Bearer {create_access_token(owner_id)}"}
    try:
        yield summary.org_id, headers, storage
    finally:
        monkeypatch.setattr(get_settings(), "demo_org_id", "", raising=False)
        async with db_sessionmaker() as session:
            from app.models import Organization

            for model, row_id in ((User, owner_id), (Organization, summary.org_id)):
                row = await session.get(model, row_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()


async def test_demo_org_mutations_are_forbidden(demo_org, db_sessionmaker: Sessionmaker) -> None:
    _, headers, _ = demo_org
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_sessionmaker] = lambda: db_sessionmaker
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            # Reads stay open.
            assert (await client.get("/api/v1/documents")).status_code == 200
            # Mutations 403 even for the owner.
            created = await client.post(
                "/api/v1/documents",
                json={"source_type": "text", "title": "x", "content": "hello"},
            )
            assert created.status_code == 403
            assert "read-only" in created.json()["detail"]
    finally:
        app.dependency_overrides.clear()


async def test_reset_job_keeps_org_id_and_replaces_content(
    demo_org, db_sessionmaker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, _, storage = demo_org
    monkeypatch.setattr("app.core.db.async_session_factory", db_sessionmaker)
    monkeypatch.setattr("app.services.storage.get_storage", lambda: storage)

    async with db_sessionmaker() as session:
        before_docs = (
            (await session.execute(select(Document.id).where(Document.org_id == org_id)))
            .scalars()
            .all()
        )
    assert before_docs

    result = await demo.reset_demo_org({})
    assert result["conversations_removed"] == 1

    async with db_sessionmaker() as session:
        after_docs = (
            (await session.execute(select(Document.id).where(Document.org_id == org_id)))
            .scalars()
            .all()
        )
        conversations = (
            (await session.execute(select(Conversation).where(Conversation.org_id == org_id)))
            .scalars()
            .all()
        )
    assert after_docs and set(after_docs) != set(before_docs), "documents replaced"
    assert conversations == []


async def test_reset_job_noops_without_demo_org_id() -> None:
    assert await demo.reset_demo_org({}) == {"skipped": "demo_org_id unset"}
