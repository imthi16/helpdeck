import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.services.retrieval import ScoredChunk


class SearchRequest(BaseModel):
    org_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)
    top_n: int = Field(default=8, ge=1, le=50)


class SearchResultChunk(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    dense_rank: int | None = None
    text_rank: int | None = None
    text_score: float | None = None
    metadata: dict[str, Any]

    @classmethod
    def from_scored(cls, chunk: ScoredChunk) -> "SearchResultChunk":
        return cls(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            content=chunk.content,
            score=chunk.score,
            dense_rank=chunk.dense_rank,
            text_rank=chunk.text_rank,
            text_score=chunk.text_score,
            metadata=chunk.metadata,
        )


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultChunk]
