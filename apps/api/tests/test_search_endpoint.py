import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import Organization
from app.routers.internal import (
    get_search_sessionmaker,
    require_internal_enabled,
)
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.retrieval import HybridRetriever
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
QUERIES_PATH = REPO_ROOT / "eval" / "fixtures" / "queries.json"

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def seeded_org(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> tuple[uuid.UUID, LocalFileStorage]:
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"seed-test-{uuid.uuid4()}",
    )
    assert summary.document_count == 16
    assert summary.chunk_count > 0
    try:
        yield summary.org_id, storage
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def test_retrieval_quality_top3(
    db_sessionmaker: Sessionmaker,
    seeded_org: tuple[uuid.UUID, LocalFileStorage],
) -> None:
    org_id, _ = seeded_org
    queries = json.loads(QUERIES_PATH.read_text())
    retriever = HybridRetriever(db_sessionmaker, EmbeddingService())

    hits = 0
    misses: list[str] = []
    for item in queries:
        results = await retriever.search(org_id, item["query"], top_n=3)
        needle = item["expected_contains"].lower()
        if any(needle in r.content.lower() for r in results):
            hits += 1
        else:
            misses.append(item["query"])

    assert hits >= 8, f"only {hits}/{len(queries)} in top-3; missed: {misses}"


async def test_internal_search_disabled_returns_404() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/search",
            json={"org_id": str(uuid.uuid4()), "query": "hello"},
        )
    assert response.status_code == 404


async def test_internal_search_enabled_returns_results(
    db_sessionmaker: Sessionmaker,
    seeded_org: tuple[uuid.UUID, LocalFileStorage],
) -> None:
    org_id, _ = seeded_org
    app.dependency_overrides[require_internal_enabled] = lambda: None
    app.dependency_overrides[get_search_sessionmaker] = lambda: db_sessionmaker
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/internal/search",
                json={"org_id": str(org_id), "query": "how often should I descale", "top_n": 3},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "how often should I descale"
        assert 1 <= len(body["results"]) <= 3
        top = body["results"][0]
        assert "score" in top and "content" in top and "chunk_id" in top
    finally:
        app.dependency_overrides.clear()
