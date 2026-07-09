"""Assemble agent dependencies and run the graph for one conversation turn."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_agent_graph
from app.agent.state import AgentDependencies, AgentState
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMGateway
from app.services.reranker import Reranker, get_reranker


def build_dependencies(
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    gateway: LLMGateway | None = None,
    embedding_service: EmbeddingService | None = None,
    reranker: Reranker | None = None,
) -> AgentDependencies:
    settings = get_settings()
    return AgentDependencies(
        gateway=gateway or LLMGateway(),
        sessionmaker=sessionmaker or async_session_factory,
        embedding_service=embedding_service or EmbeddingService(),
        reranker=reranker or get_reranker(),
        faithfulness_threshold=settings.faithfulness_threshold,
        retrieval_top_n=settings.agent_retrieval_top_n,
    )


async def run_turn(
    deps: AgentDependencies,
    *,
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    checkpointer: Any = None,
) -> AgentState:
    graph = build_agent_graph(deps, checkpointer=checkpointer)
    initial: AgentState = {
        "org_id": str(org_id),
        "conversation_id": str(conversation_id),
        "question": question,
    }
    config = {"configurable": {"thread_id": str(conversation_id)}}
    return await graph.ainvoke(initial, config=config)
