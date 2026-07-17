"""Hybrid retrieval: dense (pgvector cosine) + full-text (ts_rank_cd) fused by RRF.

Dense and full-text candidate lists are fetched in parallel, then combined with
Reciprocal Rank Fusion so a chunk that ranks well in *either* modality surfaces,
and one that ranks well in *both* rises to the top.
"""

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, func, select

from app.core.db import SessionFactory
from app.models import Chunk
from app.services.embeddings import EmbeddingService

DENSE_K = 20
TEXT_K = 20
RRF_K = 60
DEFAULT_TOP_N = 8


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hashable]],
    *,
    k: int = RRF_K,
) -> list[tuple[Hashable, float]]:
    """Fuse ranked id lists into one scored list.

    Each list contributes ``1 / (k + rank)`` per item (rank is 1-based). Higher
    fused score first; ties broken by first appearance for determinism.
    """
    scores: dict[Hashable, float] = defaultdict(float)
    first_seen: dict[Hashable, int] = {}
    order = 0
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] += 1.0 / (k + rank)
            if key not in first_seen:
                first_seen[key] = order
                order += 1
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


@dataclass
class ScoredChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    metadata: dict[str, Any]
    score: float
    dense_rank: int | None = None
    text_rank: int | None = None
    text_score: float | None = None


@dataclass
class _Candidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    text_score: float | None = None


class HybridRetriever:
    def __init__(
        self,
        sessionmaker: SessionFactory,
        embedding_service: EmbeddingService,
        *,
        dense_k: int = DENSE_K,
        text_k: int = TEXT_K,
        rrf_k: int = RRF_K,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedding_service = embedding_service
        self._dense_k = dense_k
        self._text_k = text_k
        self._rrf_k = rrf_k

    async def _dense_search(self, org_id: uuid.UUID, query_vector: list[float]) -> list[_Candidate]:
        distance = Chunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(Chunk.id, Chunk.document_id, Chunk.content, Chunk.meta)
            .where(Chunk.org_id == org_id, Chunk.embedding.isnot(None))
            .order_by(distance)
            .limit(self._dense_k)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).all()
        return [_Candidate(r.id, r.document_id, r.content, r.meta) for r in rows]

    async def _fulltext_search(self, org_id: uuid.UUID, query: str) -> list[_Candidate]:
        tsquery = func.plainto_tsquery("english", query)
        rank = func.ts_rank_cd(Chunk.content_tsv, tsquery).cast(Float)
        stmt = (
            select(Chunk.id, Chunk.document_id, Chunk.content, Chunk.meta, rank.label("rank"))
            .where(Chunk.org_id == org_id, Chunk.content_tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(self._text_k)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).all()
        return [_Candidate(r.id, r.document_id, r.content, r.meta, r.rank) for r in rows]

    async def search(
        self,
        org_id: uuid.UUID,
        query: str,
        *,
        top_n: int = DEFAULT_TOP_N,
    ) -> list[ScoredChunk]:
        query_vector = await self._embedding_service.embed_query(query)
        dense, fulltext = await asyncio.gather(
            self._dense_search(org_id, query_vector),
            self._fulltext_search(org_id, query),
        )

        dense_rank = {c.chunk_id: i + 1 for i, c in enumerate(dense)}
        text_rank = {c.chunk_id: i + 1 for i, c in enumerate(fulltext)}
        text_score = {c.chunk_id: c.text_score for c in fulltext}
        candidates: dict[uuid.UUID, _Candidate] = {}
        for candidate in (*dense, *fulltext):
            candidates.setdefault(candidate.chunk_id, candidate)

        fused = reciprocal_rank_fusion(
            [[c.chunk_id for c in dense], [c.chunk_id for c in fulltext]],
            k=self._rrf_k,
        )

        results: list[ScoredChunk] = []
        for chunk_id, score in fused[:top_n]:
            candidate = candidates[chunk_id]
            results.append(
                ScoredChunk(
                    chunk_id=candidate.chunk_id,
                    document_id=candidate.document_id,
                    content=candidate.content,
                    metadata=candidate.metadata,
                    score=score,
                    dense_rank=dense_rank.get(chunk_id),
                    text_rank=text_rank.get(chunk_id),
                    text_score=text_score.get(chunk_id),
                )
            )
        return results
