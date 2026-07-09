import uuid

import httpx
import pytest

from app.core.config import Settings
from app.services.reranker import (
    CohereReranker,
    NoopReranker,
    RerankerError,
    get_reranker,
    retrieve_reranked,
)
from app.services.retrieval import ScoredChunk


def make_chunks(n: int) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            content=f"chunk {i}",
            metadata={"i": i},
            score=1.0 / (i + 1),
        )
        for i in range(n)
    ]


async def test_noop_preserves_order_and_truncates() -> None:
    chunks = make_chunks(12)
    result = await NoopReranker().rerank("q", chunks, top_n=8)

    assert [c.chunk_id for c in result] == [c.chunk_id for c in chunks[:8]]


async def test_noop_handles_empty() -> None:
    assert await NoopReranker().rerank("q", [], top_n=8) == []


async def test_cohere_reorders_by_relevance_and_calls_provider() -> None:
    chunks = make_chunks(4)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Reverse the input order via descending relevance scores.
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 3, "relevance_score": 0.9},
                    {"index": 2, "relevance_score": 0.8},
                    {"index": 1, "relevance_score": 0.4},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reranker = CohereReranker("test-key", client=client)

    result = await reranker.rerank("q", chunks, top_n=3)

    assert len(calls) == 1  # provider was called
    assert [c.chunk_id for c in result] == [
        chunks[3].chunk_id,
        chunks[2].chunk_id,
        chunks[1].chunk_id,
    ]
    assert result[0].score == 0.9  # score replaced with relevance
    await client.aclose()


async def test_cohere_empty_shortcircuits_without_call() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reranker = CohereReranker("test-key", client=client)

    assert await reranker.rerank("q", [], top_n=8) == []
    assert calls == []  # no provider call for empty input
    await client.aclose()


def test_factory_defaults_to_noop() -> None:
    assert isinstance(get_reranker(Settings(reranker="none")), NoopReranker)
    assert isinstance(get_reranker(Settings(reranker="")), NoopReranker)


def test_factory_selects_cohere() -> None:
    reranker = get_reranker(Settings(reranker="cohere", cohere_api_key="key"))
    assert isinstance(reranker, CohereReranker)


def test_factory_cohere_without_key_raises() -> None:
    with pytest.raises(RerankerError):
        get_reranker(Settings(reranker="cohere", cohere_api_key=""))


def test_factory_unknown_raises() -> None:
    with pytest.raises(RerankerError):
        get_reranker(Settings(reranker="bge"))


class FakeRetriever:
    def __init__(self, chunks: list[ScoredChunk]) -> None:
        self._chunks = chunks
        self.requested_top_n: int | None = None

    async def search(self, org_id: object, query: str, *, top_n: int) -> list[ScoredChunk]:
        self.requested_top_n = top_n
        return self._chunks[:top_n]


async def test_retrieve_reranked_fetches_candidates_then_trims() -> None:
    retriever = FakeRetriever(make_chunks(50))
    result = await retrieve_reranked(
        retriever, NoopReranker(), uuid.uuid4(), "q", candidates=50, top_n=8
    )

    assert retriever.requested_top_n == 50  # fetched the wide candidate set
    assert len(result) == 8  # reranked down to top-8
