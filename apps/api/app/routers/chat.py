"""SSE chat endpoint. Streams a grounded agent turn and persists the exchange.

Events: ``status`` (routing/retrieving/generating), ``token``, ``citation``,
``done`` (message_id, confidence, escalated), ``error``. The assistant message is
written only once the turn completes, so a mid-stream disconnect leaves no
orphaned assistant row.
"""

import json
import uuid
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.agent.graph import build_agent_graph
from app.agent.runner import build_dependencies
from app.core.db import SessionFactory, app_session_factory, tenant_sessionmaker
from app.core.deps import MembershipDep
from app.core.logging import get_logger
from app.models import Conversation, ConversationChannel, Message, MessageRole
from app.schemas.chat import ChatRequest
from app.services.cache import CachedAnswer, ResponseCache, compute_kb_version, get_redis
from app.services.llm import LLMGateway
from app.services.tracing import start_turn

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])

HEARTBEAT_SECONDS = 15


def get_chat_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # Base factory for the tenant lane; run_chat_stream binds it to the org via
    # tenant_sessionmaker so every session below runs under RLS.
    return app_session_factory


def get_chat_checkpointer(request: Request) -> Any:
    # Set by the app lifespan (AsyncPostgresSaver); absent under the test client.
    return getattr(request.app.state, "chat_checkpointer", None)


def get_chat_gateway() -> LLMGateway:
    return LLMGateway()


def get_chat_cache() -> ResponseCache:
    return ResponseCache(get_redis())


def _sse(event: str, payload: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(event=event, data=json.dumps(payload))


async def _ensure_conversation(
    tenant_sm: SessionFactory,
    org_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    channel: ConversationChannel,
) -> uuid.UUID:
    async with tenant_sm() as session:
        if conversation_id is not None:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.org_id != org_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
                )
            return conversation.id
        conversation = Conversation(org_id=org_id, channel=channel)
        session.add(conversation)
        await session.flush()  # populate the UUID default before leaving the block
        return conversation.id


async def _persist_message(
    tenant_sm: SessionFactory,
    *,
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    model_used: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    latency_ms: int | None = None,
    trace_id: str | None = None,
) -> uuid.UUID:
    async with tenant_sm() as session:
        message = Message(
            org_id=org_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations or [],
            confidence=confidence,
            model_used=model_used,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            trace_id=trace_id,
        )
        session.add(message)
        await session.flush()  # populate the UUID default before leaving the block
        return message.id


async def _replay_cached(
    cached: CachedAnswer,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
) -> AsyncIterator[ServerSentEvent]:
    yield _sse("status", {"stage": "cached"})
    if cached.content:
        yield _sse("token", {"text": cached.content})
    for citation in cached.citations:
        yield _sse("citation", citation)
    yield _sse(
        "done",
        {
            "message_id": str(message_id),
            "conversation_id": str(conversation_id),
            "confidence": cached.confidence,
            "escalated": cached.escalated,
            "cached": True,
        },
    )


async def run_chat_stream(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    gateway: LLMGateway,
    checkpointer: Any,
    cache: ResponseCache,
    org_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None,
    channel: ConversationChannel,
    bypass_cache: bool = False,
    debug: bool = False,
    user_identifier: str | None = None,
) -> EventSourceResponse:
    """Shared SSE chat turn used by the dashboard and widget endpoints."""
    # Bind the tenant once; every session opened below runs under RLS. Sessions
    # stay short (one per persistence step) — a single transaction across the
    # whole SSE stream would pin a pooled connection for the LLM's lifetime.
    tenant_sm = tenant_sessionmaker(org_id, session_factory=sessionmaker)
    resolved_conversation_id = await _ensure_conversation(
        tenant_sm, org_id, conversation_id, channel
    )
    if user_identifier and conversation_id is None:
        async with tenant_sm() as session:
            conversation = await session.get(Conversation, resolved_conversation_id)
            if conversation is not None:
                conversation.user_identifier = user_identifier

    await _persist_message(
        tenant_sm,
        org_id=org_id,
        conversation_id=resolved_conversation_id,
        role=MessageRole.user,
        content=message,
    )

    kb_version = await compute_kb_version(tenant_sm, org_id)
    cached = None if bypass_cache else await cache.get(str(org_id), message, kb_version)
    headers = {
        "X-Accel-Buffering": "no",
        "X-HelpDeck-Cache": "hit" if cached is not None else "miss",
    }

    if cached is not None:
        # Lightweight trace so trace counts still match conversation turns.
        turn_span = start_turn(
            message,
            conversation_id=resolved_conversation_id,
            org_id=org_id,
            channel=channel.value,
            user_identifier=user_identifier,
            cached=True,
        )
        cached_trace_id = None
        if turn_span is not None:
            cached_trace_id = turn_span.trace_id
            turn_span.update(output={"answer": cached.content})
            turn_span.end()
        message_id = await _persist_message(
            tenant_sm,
            org_id=org_id,
            conversation_id=resolved_conversation_id,
            role=MessageRole.assistant,
            content=cached.content,
            citations=cached.citations,
            confidence=cached.confidence,
            model_used=cached.model_used,
            trace_id=cached_trace_id,
        )
        return EventSourceResponse(
            _replay_cached(cached, resolved_conversation_id, message_id),
            ping=HEARTBEAT_SECONDS,
            headers=headers,
        )

    async def event_stream() -> AsyncIterator[ServerSentEvent]:
        start = perf_counter()
        final: dict[str, Any] = {}
        streamed: list[str] = []
        # Root span for the whole turn: an explicit object ended in `finally`
        # (never a context manager across this generator's yields).
        turn_span = start_turn(
            message,
            conversation_id=resolved_conversation_id,
            org_id=org_id,
            channel=channel.value,
            user_identifier=user_identifier,
        )
        trace_id = turn_span.trace_id if turn_span is not None else None
        failed = False
        try:
            deps = build_dependencies(sessionmaker=tenant_sm, gateway=gateway)
            graph = build_agent_graph(deps, checkpointer=checkpointer)
            initial = {
                "org_id": str(org_id),
                "conversation_id": str(resolved_conversation_id),
                "question": message,
            }
            if turn_span is not None:
                initial["trace_id"] = turn_span.trace_id
                initial["parent_span_id"] = turn_span.id
            config = {"configurable": {"thread_id": str(resolved_conversation_id)}}

            async for mode, data in graph.astream(
                initial, config=config, stream_mode=["updates", "custom"]
            ):
                if mode == "custom":
                    if data["type"] == "status":
                        yield _sse("status", {"stage": data["value"]})
                    elif data["type"] == "token":
                        streamed.append(data["value"])
                        yield _sse("token", {"text": data["value"]})
                elif mode == "updates":
                    for delta in data.values():
                        if delta:
                            final.update(delta)

            streamed_text = "".join(streamed).strip()
            content = streamed_text or final.get("response", "")
            citations = final.get("citations", []) or []
            confidence = final.get("confidence")
            latency_ms = int((perf_counter() - start) * 1000)

            # Canned paths (chitchat / human handoff) produce no tokens; send the body.
            if not streamed_text and content:
                yield _sse("token", {"text": content})

            escalated = bool(final.get("escalated", False))
            message_id = await _persist_message(
                tenant_sm,
                org_id=org_id,
                conversation_id=resolved_conversation_id,
                role=MessageRole.assistant,
                content=content,
                citations=citations,
                confidence=confidence,
                model_used=final.get("model_used"),
                tokens_in=final.get("tokens_in"),
                tokens_out=final.get("tokens_out"),
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

            if debug:
                yield _sse(
                    "debug",
                    {
                        "intent": final.get("intent"),
                        "trace_id": trace_id,
                        "model": final.get("model_used"),
                        "confidence": confidence,
                        "latency_ms": latency_ms,
                        "tokens_in": final.get("tokens_in"),
                        "tokens_out": final.get("tokens_out"),
                        "chunks": [
                            {
                                "n": c.get("n"),
                                "document_title": c.get("document_title"),
                                "score": c.get("score"),
                                "snippet": c.get("content", "")[:200],
                            }
                            for c in final.get("chunks", []) or []
                        ],
                    },
                )

            # Cache only settled (non-escalated) answers so escalations always
            # re-run the agent and record a fresh escalation row.
            if not escalated and content:
                await cache.set(
                    str(org_id),
                    message,
                    kb_version,
                    CachedAnswer(
                        content=content,
                        citations=citations,
                        confidence=confidence,
                        escalated=escalated,
                        model_used=final.get("model_used"),
                    ),
                )

            for citation in citations:
                yield _sse("citation", citation)

            yield _sse(
                "done",
                {
                    "message_id": str(message_id),
                    "conversation_id": str(resolved_conversation_id),
                    "confidence": confidence,
                    "escalated": escalated,
                    "cached": False,
                    "trace_id": trace_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface any failure as an SSE error
            failed = True
            if turn_span is not None:
                turn_span.update(level="ERROR", status_message=str(exc))
            logger.exception("chat_stream_failed", conversation_id=str(resolved_conversation_id))
            yield _sse("error", {"detail": str(exc)})
        finally:
            if turn_span is not None:
                if not failed:
                    turn_span.update(
                        output={
                            "answer": final.get("response", ""),
                            "confidence": final.get("confidence"),
                            "escalated": bool(final.get("escalated", False)),
                        }
                    )
                turn_span.end()

    return EventSourceResponse(event_stream(), ping=HEARTBEAT_SECONDS, headers=headers)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    membership: MembershipDep,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_chat_sessionmaker)],
    checkpointer: Annotated[Any, Depends(get_chat_checkpointer)],
    gateway: Annotated[LLMGateway, Depends(get_chat_gateway)],
    cache: Annotated[ResponseCache, Depends(get_chat_cache)],
) -> EventSourceResponse:
    # The org comes from the caller's membership — a body org_id is accepted
    # for backward compatibility but must match (never trusted on its own).
    if request.org_id is not None and request.org_id != membership.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member of this organization"
        )
    return await run_chat_stream(
        sessionmaker=sessionmaker,
        gateway=gateway,
        checkpointer=checkpointer,
        cache=cache,
        org_id=membership.org_id,
        message=request.message,
        conversation_id=request.conversation_id,
        channel=request.channel,
        bypass_cache=request.bypass_cache,
        debug=request.debug,
    )
