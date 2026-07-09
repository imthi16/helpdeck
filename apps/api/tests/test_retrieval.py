import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Chunk, Document, DocumentSourceType, DocumentStatus, Organization
from app.services.embeddings import EmbeddingService
from app.services.retrieval import HybridRetriever, reciprocal_rank_fusion

EMBED_DIMS = 1536
Sessionmaker = async_sessionmaker[AsyncSession]


# --- RRF math (pure) ---------------------------------------------------------


def test_rrf_basic_scores_and_order() -> None:
    fused = dict(reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60))

    assert fused["b"] == 1 / 61 + 1 / 62
    assert fused["c"] == 1 / 62 + 1 / 63
    assert fused["a"] == 1 / 61
    assert fused["d"] == 1 / 63

    order = [key for key, _ in reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]])]
    assert order == ["b", "c", "a", "d"]


def test_rrf_empty_and_single() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []
    assert reciprocal_rank_fusion([["x"]], k=60) == [("x", 1 / 61)]


def test_rrf_tie_broken_by_first_appearance() -> None:
    # "x" and "y" each appear once at rank 1 -> equal score, stable order.
    fused = reciprocal_rank_fusion([["x"], ["y"]], k=60)
    assert [key for key, _ in fused] == ["x", "y"]
    assert fused[0][1] == fused[1][1]


# --- Hybrid search (integration, deterministic embeddings) -------------------


def _unit_vector(*hot_dims: int) -> list[float]:
    vector = [0.0] * EMBED_DIMS
    for dim in hot_dims:
        vector[dim] = 1.0
    return vector


class MappedQueryProvider:
    """Returns a fixed vector per exact query string (stand-in for a real model)."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [self._mapping[text] for text in texts]


async def _seed_corpus(sessionmaker: Sessionmaker) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    async with sessionmaker() as session:
        org = Organization(name=f"org-{uuid.uuid4()}")
        session.add(org)
        await session.flush()
        document = Document(
            org_id=org.id,
            title="KB",
            source_type=DocumentSourceType.text,
            status=DocumentStatus.ready,
        )
        session.add(document)
        await session.flush()

        chunks = {
            "refund": (
                "To return an item, contact support for a prepaid label. "
                "Refunds are issued within five business days.",
                _unit_vector(0),
            ),
            "shipping": (
                "Standard delivery takes two to four business days across the "
                "European Union after dispatch.",
                _unit_vector(1),
            ),
            "warranty": (
                "The espresso machine includes a two year manufacturer warranty "
                "against defects in materials.",
                _unit_vector(2),
            ),
        }
        ids: dict[str, uuid.UUID] = {}
        for name, (content, vector) in chunks.items():
            chunk = Chunk(
                org_id=org.id,
                document_id=document.id,
                content=content,
                embedding=vector,
                meta={"name": name},
                token_count=len(content.split()),
            )
            session.add(chunk)
            await session.flush()
            ids[name] = chunk.id
        await session.commit()
        return org.id, ids


async def _delete_org(sessionmaker: Sessionmaker, org_id: uuid.UUID) -> None:
    async with sessionmaker() as session:
        org = await session.get(Organization, org_id)
        if org is not None:
            await session.delete(org)
            await session.commit()


async def test_keyword_query_surfaces_right_chunk(db_sessionmaker: Sessionmaker) -> None:
    org_id, ids = await _seed_corpus(db_sessionmaker)
    # Keyword query embedding is orthogonal to every chunk, so full-text alone
    # must pick the winner: only the warranty chunk contains these terms.
    provider = MappedQueryProvider({"warranty defects": _unit_vector(100)})
    retriever = HybridRetriever(db_sessionmaker, EmbeddingService(provider, model="t"))

    try:
        results = await retriever.search(org_id, "warranty defects", top_n=3)
        top_ids = [r.chunk_id for r in results]
        assert ids["warranty"] in top_ids
        assert results[0].chunk_id == ids["warranty"]
    finally:
        await _delete_org(db_sessionmaker, org_id)


async def test_paraphrase_query_surfaces_right_chunk(db_sessionmaker: Sessionmaker) -> None:
    org_id, ids = await _seed_corpus(db_sessionmaker)
    # Paraphrase shares no keywords with the refund chunk (full-text finds
    # nothing), so dense semantic similarity must surface it.
    provider = MappedQueryProvider({"how do i get my money back": _unit_vector(0)})
    retriever = HybridRetriever(db_sessionmaker, EmbeddingService(provider, model="t"))

    try:
        results = await retriever.search(org_id, "how do i get my money back", top_n=3)
        assert results, "expected at least one result"
        assert results[0].chunk_id == ids["refund"]
        assert results[0].dense_rank == 1
        assert results[0].text_rank is None  # full-text matched nothing
    finally:
        await _delete_org(db_sessionmaker, org_id)
