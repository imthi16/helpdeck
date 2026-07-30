"""Assemble agent dependencies and run the graph for one conversation turn."""

import uuid
from typing import Any

from app.agent.graph import build_agent_graph
from app.agent.state import AgentDependencies, AgentState
from app.core.config import get_settings
from app.core.db import SessionFactory, unscoped_session_factory
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMGateway
from app.services.reranker import Reranker, get_reranker


def build_dependencies(
    *,
    session_factory: SessionFactory | None = None,
    gateway: LLMGateway | None = None,
    embedding_service: EmbeddingService | None = None,
    reranker: Reranker | None = None,
) -> AgentDependencies:
    settings = get_settings()
    return AgentDependencies(
        gateway=gateway or LLMGateway(),
        # The request path always passes a tenant-scoped factory; the fallback is
        # a privileged unit of work for offline callers (eval, scripts).
        session_factory=session_factory or unscoped_session_factory(),
        embedding_service=embedding_service or EmbeddingService(),
        reranker=reranker or get_reranker(),
        faithfulness_threshold=settings.faithfulness_threshold,
        retrieval_top_n=settings.agent_retrieval_top_n,
    )


async def run_turn(
    deps: AgentDependencies,
    *,
    org_id: uuid.UUID,
    question: str,
    conversation_id: uuid.UUID | None = None,
    checkpointer: Any = None,
) -> AgentState:
    """Run one turn. ``conversation_id`` is optional so the eval harness (6.3) can
    exercise the real graph without writing conversations or escalations: with no
    conversation in state the ``escalate`` node skips its DB write.
    """
    graph = build_agent_graph(deps, checkpointer=checkpointer)
    initial: AgentState = {"org_id": str(org_id), "question": question}
    if conversation_id is not None:
        initial["conversation_id"] = str(conversation_id)
    # Threads are per conversation; an anonymous turn gets a throwaway thread so
    # concurrent eval items can never share checkpointed state.
    thread_id = str(conversation_id) if conversation_id is not None else str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(initial, config=config)
