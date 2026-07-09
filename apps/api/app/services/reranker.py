"""Optional reranking of fused retrieval candidates.

The default ``NoopReranker`` preserves the RRF order. When ``RERANKER=cohere``,
``CohereReranker`` reorders the fused top candidates by cross-encoder relevance.
Intended flow: fetch fused top-50 -> rerank -> top-8.
"""

from dataclasses import replace
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.services.retrieval import ScoredChunk

RERANK_CANDIDATES = 50
RERANK_TOP_N = 8

COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
COHERE_DEFAULT_MODEL = "rerank-english-v3.0"


class Reranker(Protocol):
    async def rerank(
        self, query: str, chunks: list[ScoredChunk], *, top_n: int = RERANK_TOP_N
    ) -> list[ScoredChunk]: ...


class NoopReranker:
    async def rerank(
        self, query: str, chunks: list[ScoredChunk], *, top_n: int = RERANK_TOP_N
    ) -> list[ScoredChunk]:
        return chunks[:top_n]


class RerankerError(Exception):
    pass


class CohereReranker:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = COHERE_DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise RerankerError("RERANKER=cohere requires COHERE_API_KEY")
        self._api_key = api_key
        self._model = model
        self._client = client

    async def rerank(
        self, query: str, chunks: list[ScoredChunk], *, top_n: int = RERANK_TOP_N
    ) -> list[ScoredChunk]:
        if not chunks:
            return []

        payload = {
            "model": self._model,
            "query": query,
            "documents": [chunk.content for chunk in chunks],
            "top_n": min(top_n, len(chunks)),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(COHERE_RERANK_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        finally:
            if owns_client:
                await client.aclose()

        reranked: list[ScoredChunk] = []
        for result in data["results"]:
            chunk = chunks[result["index"]]
            reranked.append(replace(chunk, score=float(result["relevance_score"])))
        return reranked[:top_n]


def get_reranker(settings: Settings | None = None) -> Reranker:
    settings = settings or get_settings()
    name = settings.reranker.strip().lower()
    if name in ("", "none"):
        return NoopReranker()
    if name == "cohere":
        return CohereReranker(settings.cohere_api_key)
    raise RerankerError(f"unsupported RERANKER: {settings.reranker!r}")


async def retrieve_reranked(
    retriever: "RetrieverLike",
    reranker: Reranker,
    org_id: object,
    query: str,
    *,
    candidates: int = RERANK_CANDIDATES,
    top_n: int = RERANK_TOP_N,
) -> list[ScoredChunk]:
    """Fetch fused top-``candidates`` then rerank down to ``top_n``."""
    fused = await retriever.search(org_id, query, top_n=candidates)
    return await reranker.rerank(query, fused, top_n=top_n)


class RetrieverLike(Protocol):
    async def search(self, org_id: object, query: str, *, top_n: int) -> list[ScoredChunk]: ...
