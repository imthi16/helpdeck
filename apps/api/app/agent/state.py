"""Agent graph state and dependency container."""

from dataclasses import dataclass
from typing import Any, TypedDict

from app.core.db import SessionFactory
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
    # Langfuse propagation (empty when tracing is off).
    trace_id: str
    parent_span_id: str
    intent: str
    chunks: list[dict[str, Any]]
    answer: str
    citations: list[dict[str, Any]]
    confidence: float
    model_used: str
    tokens_in: int
    tokens_out: int
    escalated: bool
    escalation_reason: str
    response: str


@dataclass
class AgentDependencies:
    gateway: LLMGateway
    # Transaction-owning session factory (tenant-bound in production): helpers
    # must not commit inside the block.
    sessionmaker: SessionFactory
    embedding_service: EmbeddingService
    reranker: Reranker
    faithfulness_threshold: float
    retrieval_top_n: int
