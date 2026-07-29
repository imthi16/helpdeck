"""Langfuse tracing facade — the only module allowed to import the Langfuse SDK.

Tracing is a strict no-op when ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are unset, so dev, tests and CI run untouched. Callers use:

- ``turn_trace`` — root span for one conversation turn; carries ``org_id`` and
  ``conversation_id`` on every child span and uses the conversation as the
  Langfuse session, so a whole thread reads as one session.
- ``observation`` — nested span/generation/retriever/evaluator inside a turn.
- ``score_trace`` / ``score_session`` — attach user feedback (thumbs, CSAT) as
  Langfuse scores after the turn is long over.

Instrumentation must never break a request: score emission is best-effort and a
failure is logged, not raised.
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol

from langfuse import Langfuse, propagate_attributes

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ObservationType = Literal[
    "span", "generation", "embedding", "retriever", "evaluator", "agent", "tool", "chain"
]
ScoreDataType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]


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


class Observation(Protocol):
    """The slice of the Langfuse span API this app uses."""

    def update(self, **fields: Any) -> Any: ...


class NoopObservation:
    """Stands in for a span when tracing is disabled, so callers stay branchless."""

    def update(self, **fields: Any) -> "NoopObservation":
        return self


@contextlib.contextmanager
def observation(
    name: str, *, as_type: ObservationType = "span", **fields: Any
) -> Iterator[Observation]:
    """Span nested under whatever observation is currently active."""
    client = get_langfuse()
    if client is None:
        yield NoopObservation()
        return
    with client.start_as_current_observation(name=name, as_type=as_type, **fields) as span:
        yield span


@dataclass
class TurnTrace:
    """Handle on the root span of one conversation turn.

    ``trace_id`` is ``None`` when tracing is disabled; persist it alongside the
    assistant message so later feedback can be scored onto this trace.
    """

    trace_id: str | None
    _span: Observation

    def update(self, **fields: Any) -> None:
        self._span.update(**fields)

    def score(
        self,
        name: str,
        value: float | str,
        *,
        data_type: ScoreDataType | None = None,
        comment: str | None = None,
    ) -> None:
        score_trace(self.trace_id, name=name, value=value, data_type=data_type, comment=comment)


@contextlib.contextmanager
def turn_trace(
    name: str,
    *,
    org_id: str,
    conversation_id: str,
    user_id: str | None = None,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[TurnTrace]:
    """Root span for one conversation turn, with tenant metadata propagated.

    Attributes are propagated (not just set on the root span) because Langfuse
    aggregations — cost per org, per session — only count observations that carry
    the attribute.
    """
    client = get_langfuse()
    if client is None:
        yield TurnTrace(None, NoopObservation())
        return
    with client.start_as_current_observation(name=name, as_type="agent", input=input) as span:
        with propagate_attributes(
            trace_name=name,
            session_id=conversation_id,
            user_id=user_id,
            tags=tags,
            metadata={"org_id": org_id, "conversation_id": conversation_id, **(metadata or {})},
        ):
            yield TurnTrace(client.get_current_trace_id(), span)


def score_trace(
    trace_id: str | None,
    *,
    name: str,
    value: float | str,
    data_type: ScoreDataType | None = None,
    comment: str | None = None,
) -> None:
    """Attach a score to a finished trace (no-op without tracing or a trace id)."""
    client = get_langfuse()
    if client is None or not trace_id:
        return
    _create_score(
        client, name=name, value=value, trace_id=trace_id, data_type=data_type, comment=comment
    )


def score_session(
    session_id: str,
    *,
    name: str,
    value: float | str,
    data_type: ScoreDataType | None = None,
    comment: str | None = None,
) -> None:
    """Attach a score to a whole session — the home for conversation-level CSAT."""
    client = get_langfuse()
    if client is None:
        return
    _create_score(
        client, name=name, value=value, session_id=session_id, data_type=data_type, comment=comment
    )


def _create_score(client: Any, **kwargs: Any) -> None:
    try:
        client.create_score(**kwargs)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the request
        logger.warning("langfuse_score_failed", name=kwargs.get("name"), error=str(exc))


def flush() -> None:
    """Force-send buffered events — for short-lived processes (jobs, eval runs)."""
    client = get_langfuse()
    if client is not None:
        client.flush()
