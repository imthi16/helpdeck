"""Langfuse tracing helpers. Everything is a strict no-op when keys are unset.

Layout of a chat turn (task 6.1):

- ``start_turn`` creates the root ``chat.turn`` span as an *explicit object*
  (never a context manager) because the SSE handler is an async generator —
  holding a contextvar-based span across its yields corrupts the OTEL
  context. The caller ends it in the stream's ``finally``.
- Trace attributes (session = conversation, user, org metadata, channel tag)
  are applied with ``propagate_attributes`` *around span creation only* —
  a synchronous block with no yields inside.
- Graph nodes use ``node_span`` (``start_as_current_observation`` with an
  explicit ``trace_context``): node coroutines run start-to-finish in their
  own task, so current-context is safe there, and the LLM/embedding
  generations created inside automatically parent under the node span.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from langfuse import Langfuse, propagate_attributes
from langfuse.types import TraceContext

from app.core.config import get_settings


@lru_cache
def get_langfuse() -> Langfuse | None:
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host or "https://cloud.langfuse.com",
    )


def start_turn(
    question: str,
    *,
    conversation_id: uuid.UUID,
    org_id: uuid.UUID,
    channel: str,
    user_identifier: str | None = None,
    cached: bool = False,
) -> Any | None:
    """Root span for one chat turn; returns None when tracing is off.

    The caller owns the span: call ``.update(output=...)`` and ``.end()`` when
    the turn finishes (in the stream's ``finally`` for the live path).
    """
    langfuse = get_langfuse()
    if langfuse is None:
        return None
    with propagate_attributes(
        session_id=str(conversation_id),
        user_id=user_identifier,
        tags=[channel],
        metadata={"org_id": str(org_id), "conversation_id": str(conversation_id)},
    ):
        return langfuse.start_observation(
            name="chat.turn",
            as_type="span",
            input={"question": question},
            metadata={"cached": cached},
        )


@contextmanager
def node_span(state: dict[str, Any], name: str, **fields: Any) -> Iterator[Any | None]:
    """Current-context span for one agent-graph node, parented to the turn.

    Safe inside node coroutines (fully awaited, no generator yields). Yields
    None—and does nothing—when tracing is off or the turn wasn't traced.
    """
    langfuse = get_langfuse()
    trace_id = state.get("trace_id")
    if langfuse is None or not trace_id:
        yield None
        return
    with langfuse.start_as_current_observation(
        name=name,
        as_type="span",
        trace_context=TraceContext(trace_id=trace_id, parent_span_id=state.get("parent_span_id")),
        **fields,
    ) as span:
        yield span


def record_score(
    *,
    name: str,
    value: float,
    trace_id: str | None = None,
    session_id: str | None = None,
    comment: str | None = None,
) -> None:
    """Attach a score (thumbs, CSAT, online eval) to a trace or session."""
    langfuse = get_langfuse()
    if langfuse is None or not (trace_id or session_id):
        return
    langfuse.create_score(
        name=name, value=value, trace_id=trace_id, session_id=session_id, comment=comment
    )
