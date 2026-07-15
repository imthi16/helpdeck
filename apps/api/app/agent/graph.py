"""LangGraph agent: router -> retrieve -> answer -> faithfulness_judge -> respond|escalate.

The grounded contract is enforced structurally: the answer node only keeps
citations that map to a real retrieved chunk, and the gate escalates whenever the
answer is ungrounded (no valid citations) or the faithfulness score is below the
configured threshold. ``human_request`` short-circuits straight to escalation.
"""

import re
import uuid
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agent import prompts
from app.agent.state import AgentDependencies, AgentState, chunk_to_dict
from app.core.logging import get_logger
from app.models import Conversation, ConversationStatus, Escalation
from app.services.ingestion.chunker import count_tokens
from app.services.llm import LLMMessage, LLMRoute
from app.services.retrieval import HybridRetriever

logger = get_logger(__name__)

VALID_INTENTS = {"faq", "chitchat", "human_request"}
_CITATION_RE = re.compile(r"\[(\d+)\]")
_NUMBER_RE = re.compile(r"\d*\.?\d+")


def parse_intent(text: str) -> str:
    lowered = text.strip().lower()
    for intent in VALID_INTENTS:
        if intent in lowered:
            return intent
    return "faq"


def parse_citations(answer: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[int] = set()
    for match in _CITATION_RE.finditer(answer):
        n = int(match.group(1))
        if n in seen or not (1 <= n <= len(chunks)):
            continue
        seen.add(n)
        chunk = chunks[n - 1]
        citations.append(
            {
                "n": n,
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "document_title": chunk["document_title"],
                "snippet": chunk["content"][:200],
            }
        )
    return citations


def parse_confidence(text: str) -> float:
    match = _NUMBER_RE.search(text)
    if not match:
        return 0.0
    try:
        return max(0.0, min(1.0, float(match.group(0))))
    except ValueError:
        return 0.0


def _emit(event_type: str, **fields: Any) -> None:
    """Emit a custom stream event when running under a streaming invocation.

    A no-op outside custom streaming (e.g. ainvoke), so nodes stay pure there.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    if writer is not None:
        writer({"type": event_type, **fields})


def build_agent_graph(deps: AgentDependencies, checkpointer: Any = None):
    async def router(state: AgentState) -> AgentState:
        _emit("status", value="routing")
        response = await deps.gateway.complete(
            [
                LLMMessage("system", prompts.ROUTER_SYSTEM),
                LLMMessage("user", state["question"]),
            ],
            route=LLMRoute.cheap,
        )
        return {"intent": parse_intent(response.text)}

    async def retrieve(state: AgentState) -> AgentState:
        _emit("status", value="retrieving")
        retriever = HybridRetriever(deps.sessionmaker, deps.embedding_service)
        fused = await retriever.search(
            uuid.UUID(state["org_id"]), state["question"], top_n=max(50, deps.retrieval_top_n)
        )
        reranked = await deps.reranker.rerank(state["question"], fused, top_n=deps.retrieval_top_n)
        chunks = [chunk_to_dict(chunk, i) for i, chunk in enumerate(reranked, start=1)]
        return {"chunks": chunks}

    async def answer(state: AgentState) -> AgentState:
        chunks = state.get("chunks", [])
        if not chunks:
            return {"answer": prompts.REFUSAL_TEXT, "citations": []}
        _emit("status", value="generating")
        messages = [
            LLMMessage("system", prompts.GROUNDED_SYSTEM),
            LLMMessage("user", prompts.build_answer_prompt(state["question"], chunks)),
        ]
        pieces: list[str] = []
        async for piece in deps.gateway.stream(messages, route=LLMRoute.strong):
            pieces.append(piece)
            _emit("token", value=piece)
        text = "".join(pieces).strip()
        citations = parse_citations(text, chunks)
        prompt_text = "\n".join(m.content for m in messages)
        return {
            "answer": text,
            "citations": citations,
            "model_used": deps.gateway.model_for(LLMRoute.strong),
            "tokens_in": count_tokens(prompt_text),
            "tokens_out": count_tokens(text),
        }

    async def faithfulness_judge(state: AgentState) -> AgentState:
        if not state.get("citations"):
            # Ungrounded answers skip the judge; the gate will escalate them.
            return {"confidence": 0.0}
        response = await deps.gateway.complete(
            [
                LLMMessage("system", prompts.JUDGE_SYSTEM),
                LLMMessage(
                    "user",
                    prompts.build_judge_prompt(state["answer"], state.get("chunks", [])),
                ),
            ],
            route=LLMRoute.cheap,
        )
        return {"confidence": parse_confidence(response.text)}

    async def chitchat(state: AgentState) -> AgentState:
        return {"response": prompts.CHITCHAT_REPLY, "answer": prompts.CHITCHAT_REPLY}

    async def respond(state: AgentState) -> AgentState:
        return {"response": state["answer"], "escalated": False}

    async def escalate(state: AgentState) -> AgentState:
        reason = _escalation_reason(state, deps.faithfulness_threshold)
        conversation_id = state.get("conversation_id")
        if conversation_id:
            await _record_escalation(deps, state, reason, uuid.UUID(conversation_id))
        return {
            "escalated": True,
            "escalation_reason": reason,
            "response": prompts.HANDOFF_TEXT,
        }

    def route_after_router(state: AgentState) -> str:
        return {
            "human_request": "escalate",
            "chitchat": "chitchat",
        }.get(state["intent"], "retrieve")

    def gate_after_judge(state: AgentState) -> str:
        if not state.get("citations"):
            return "escalate"
        if state.get("confidence", 0.0) < deps.faithfulness_threshold:
            return "escalate"
        return "respond"

    builder = StateGraph(AgentState)
    builder.add_node("router", router)
    builder.add_node("retrieve", retrieve)
    builder.add_node("answer", answer)
    builder.add_node("faithfulness_judge", faithfulness_judge)
    builder.add_node("chitchat", chitchat)
    builder.add_node("respond", respond)
    builder.add_node("escalate", escalate)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {"escalate": "escalate", "chitchat": "chitchat", "retrieve": "retrieve"},
    )
    builder.add_edge("retrieve", "answer")
    builder.add_edge("answer", "faithfulness_judge")
    builder.add_conditional_edges(
        "faithfulness_judge",
        gate_after_judge,
        {"respond": "respond", "escalate": "escalate"},
    )
    builder.add_edge("chitchat", END)
    builder.add_edge("respond", END)
    builder.add_edge("escalate", END)

    return builder.compile(checkpointer=checkpointer)


def _escalation_reason(state: AgentState, threshold: float) -> str:
    if state.get("intent") == "human_request":
        return "customer requested a human"
    if not state.get("citations"):
        return "no supporting context found (possible out-of-scope question)"
    return f"faithfulness {state.get('confidence', 0.0):.2f} below threshold {threshold:.2f}"


async def _record_escalation(
    deps: AgentDependencies,
    state: AgentState,
    reason: str,
    conversation_id: uuid.UUID,
) -> None:
    async with deps.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            logger.warning("escalation_no_conversation", conversation_id=str(conversation_id))
            return
        conversation.status = ConversationStatus.escalated
        session.add(
            Escalation(
                org_id=conversation.org_id,
                conversation_id=conversation_id,
                reason=reason,
            )
        )
