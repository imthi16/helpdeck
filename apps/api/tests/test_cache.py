import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentSourceType, DocumentStatus, Organization
from app.services.cache import (
    CachedAnswer,
    ResponseCache,
    cache_key,
    compute_kb_version,
    get_redis,
    normalize_query,
)

Sessionmaker = async_sessionmaker[AsyncSession]


def test_normalize_query_collapses_whitespace_and_case() -> None:
    assert normalize_query("  How   OFTEN  to Descale? ") == "how often to descale?"


def test_cache_key_stable_and_sensitive() -> None:
    org = str(uuid.uuid4())
    base = cache_key(org, "how often to descale", "v1")
    assert base == cache_key(org, "How  often to descale", "v1")  # normalization
    assert base != cache_key(org, "how often to descale", "v2")  # kb version
    assert base != cache_key(str(uuid.uuid4()), "how often to descale", "v1")  # org


def test_cached_answer_round_trip() -> None:
    answer = CachedAnswer(
        content="Descale every three months [1].",
        citations=[{"n": 1, "chunk_id": "c1"}],
        confidence=0.9,
        escalated=False,
        model_used="m",
    )
    assert CachedAnswer.from_json(answer.to_json()) == answer


@pytest.fixture
async def redis_cache():
    client = get_redis()
    cache = ResponseCache(client, ttl_seconds=30)
    yield cache
    await client.aclose()


async def test_cache_get_miss_then_set_hit(redis_cache: ResponseCache) -> None:
    org = str(uuid.uuid4())
    query = "how often to descale"
    version = "v1"

    assert await redis_cache.get(org, query, version) is None

    answer = CachedAnswer(
        content="Every three months [1].",
        citations=[{"n": 1}],
        confidence=0.95,
        escalated=False,
    )
    await redis_cache.set(org, query, version, answer)

    hit = await redis_cache.get(org, query, version)
    assert hit == answer
    # A different KB version misses (invalidation on re-ingest).
    assert await redis_cache.get(org, query, "v2") is None


async def test_compute_kb_version_changes_when_kb_changes(
    db_sessionmaker: Sessionmaker,
) -> None:
    async with db_sessionmaker() as session:
        org = Organization(name=f"kbver-{uuid.uuid4()}")
        session.add(org)
        await session.flush()
        org_id = org.id
        session.add(
            Document(
                org_id=org_id,
                title="doc1",
                source_type=DocumentSourceType.text,
                status=DocumentStatus.ready,
            )
        )
        await session.commit()

    try:
        version_1 = await compute_kb_version(db_sessionmaker, org_id)

        async with db_sessionmaker() as session:
            session.add(
                Document(
                    org_id=org_id,
                    title="doc2",
                    source_type=DocumentSourceType.text,
                    status=DocumentStatus.ready,
                )
            )
            await session.commit()

        version_2 = await compute_kb_version(db_sessionmaker, org_id)
        assert version_1 != version_2
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, org_id)
            await session.delete(org)
            await session.commit()
