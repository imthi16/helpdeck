"""Agent graph state and dependency container."""

from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.embeddings import EmbeddingService
from app.services.llm import LLMGateway
from app.services.reranker import Reranker
from app.services.retrieval import ScoredChunk


def chunk_to_dict(chunk: ScoredChunk, index: int) -> dict[str, Any]:
    """JSON-serializable view of a retrieved chunk (checkpointer-safe)."""
    return {
        "n": index,
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "document_title": chunk.metadata.get("document_title", ""),
        "content": chunk.content,
        "score": chunk.score,
    }


class AgentState(TypedDict, total=False):
    org_id: str
    conversation_id: str
    question: str
    intent: str
    chunks: list[dict[str, Any]]
    answer: str
    citations: list[dict[str, Any]]
    confidence: float
    model_used: str
    escalated: bool
    escalation_reason: str
    response: str


@dataclass
class AgentDependencies:
    gateway: LLMGateway
    sessionmaker: async_sessionmaker[AsyncSession]
    embedding_service: EmbeddingService
    reranker: Reranker
    faithfulness_threshold: float
    retrieval_top_n: int
