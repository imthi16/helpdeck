"""Tracing facade: no-op without keys, and a complete span tree with keys.

The Langfuse SDK is never exercised against the network here — a recording fake
stands in for the client, so these tests assert the shape of what we send
(spans, tenant metadata, usage, scores) rather than Langfuse's own behaviour.
"""

import contextlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runner import build_dependencies, run_turn
from app.core.db import TenantSessionFactory
from app.models import Conversation, ConversationChannel, Organization
from app.services import tracing
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
APP_DIR = Path(__file__).resolve().parents[1] / "app"
Sessionmaker = async_sessionmaker[AsyncSession]


# --- recording fake ----------------------------------------------------------


@dataclass
class RecordedSpan:
    name: str
    as_type: str
    fields: dict[str, Any]
    updates: list[dict[str, Any]] = field(default_factory=list)
    children: list["RecordedSpan"] = field(default_factory=list)

    def update(self, **fields: Any) -> "RecordedSpan":
        self.updates.append(fields)
        return self

    @property
    def merged(self) -> dict[str, Any]:
        """Everything sent for this span, creation fields plus updates."""
        merged = dict(self.fields)
        for update in self.updates:
            merged.update(update)
        return merged


class RecordingLangfuse:
    """Stand-in for the Langfuse client that records the span tree and scores."""

    TRACE_ID = "0af7651916cd43dd8448eb211c80319c"

    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []  # flat, in creation order
        self.roots: list[RecordedSpan] = []
        self.scores: list[dict[str, Any]] = []
        self.flushed = 0
        self._stack: list[RecordedSpan] = []

    @contextlib.contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str = "span", **fields: Any):
        span = RecordedSpan(name=name, as_type=as_type, fields=fields)
        (self._stack[-1].children if self._stack else self.roots).append(span)
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        finally:
            self._stack.pop()

    def get_current_trace_id(self) -> str:
        return self.TRACE_ID

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushed += 1

    # helpers for assertions
    def named(self, name: str) -> RecordedSpan:
        for span in self.spans:
            if span.name == name:
                return span
        raise AssertionError(f"no span named {name}; got {[s.name for s in self.spans]}")

    def score_named(self, name: str) -> dict[str, Any]:
        for score in self.scores:
            if score.get("name") == name:
                return score
        raise AssertionError(f"no score named {name}; got {[s.get('name') for s in self.scores]}")


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> RecordingLangfuse:
    client = RecordingLangfuse()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: client)
    return client


# --- facade ------------------------------------------------------------------


def test_tracing_is_a_noop_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "get_langfuse", lambda: None)

    with tracing.observation("llm.complete", as_type="generation", model="m") as span:
        assert isinstance(span, tracing.NoopObservation)
        span.update(output="text", usage_details={"input": 1, "output": 2})

    with tracing.turn_trace("chat.turn", org_id="o", conversation_id="c") as turn:
        assert turn.trace_id is None
        turn.update(output="answer")
        turn.score("faithfulness", 0.9)

    # Scores without a client are dropped, not raised.
    tracing.score_trace("trace-1", name="user_feedback", value=1)
    tracing.score_session("session-1", name="csat", value=5)
    tracing.flush()


def test_turn_trace_carries_tenant_metadata_and_nests_children(
    langfuse: RecordingLangfuse,
) -> None:
    conversation_id = str(uuid.uuid4())
    with tracing.turn_trace(
        "chat.turn",
        org_id="org-1",
        conversation_id=conversation_id,
        user_id="visitor-7",
        input={"question": "hi"},
        metadata={"channel": "widget"},
        tags=["widget"],
    ) as turn:
        assert turn.trace_id == RecordingLangfuse.TRACE_ID
        with tracing.observation("agent.router") as span:
            span.update(output={"intent": "faq"})
        turn.update(output={"answer": "hello"})
        turn.score("faithfulness", 0.91, data_type="NUMERIC")

    root = langfuse.roots[0]
    assert root.name == "chat.turn"
    assert root.as_type == "agent"
    assert root.fields["input"] == {"question": "hi"}
    assert root.merged["output"] == {"answer": "hello"}
    # Node spans hang off the turn, so Langfuse shows one tree per turn.
    assert [child.name for child in root.children] == ["agent.router"]

    score = langfuse.score_named("faithfulness")
    assert score["value"] == 0.91
    assert score["trace_id"] == RecordingLangfuse.TRACE_ID


def test_scores_target_trace_or_session(langfuse: RecordingLangfuse) -> None:
    tracing.score_trace("trace-9", name="user_feedback", value=1, data_type="BOOLEAN")
    tracing.score_session("conv-9", name="csat", value=4, data_type="NUMERIC")
    tracing.score_trace(None, name="user_feedback", value=0)  # no trace -> dropped

    assert langfuse.score_named("user_feedback")["trace_id"] == "trace-9"
    assert langfuse.score_named("csat")["session_id"] == "conv-9"
    assert len(langfuse.scores) == 2


def test_score_failure_does_not_propagate(
    langfuse: RecordingLangfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> None:
        raise RuntimeError("langfuse is down")

    monkeypatch.setattr(langfuse, "create_score", boom)
    tracing.score_trace("trace-1", name="user_feedback", value=1)  # must not raise


def test_only_the_facade_imports_the_langfuse_sdk() -> None:
    """Everything else goes through this module, so tracing stays switchable."""
    pattern = re.compile(r"^\s*(?:import\s+langfuse\b|from\s+langfuse\b)", re.MULTILINE)
    violations = [
        path.relative_to(APP_DIR).as_posix()
        for path in APP_DIR.rglob("*.py")
        if pattern.search(path.read_text())
        and path.relative_to(APP_DIR).as_posix() != "services/tracing.py"
    ]
    assert not violations, f"langfuse imported outside the tracing facade: {violations}"


# --- the agent turn produces a complete tree --------------------------------


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker, tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"tracing-test-{uuid.uuid4()}",
    )
    try:
        yield summary.org_id
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def test_agent_turn_traces_every_node_with_chunks_and_usage(
    db_sessionmaker: Sessionmaker,
    app_sessionmaker: Sessionmaker,
    seeded: uuid.UUID,
    langfuse: RecordingLangfuse,
) -> None:
    org_id = seeded
    async with db_sessionmaker() as session:
        conversation = Conversation(org_id=org_id, channel=ConversationChannel.playground)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    deps = build_dependencies(
        session_factory=TenantSessionFactory(org_id, session_factory=app_sessionmaker)
    )
    result = await run_turn(
        deps,
        org_id=org_id,
        conversation_id=conversation_id,
        question="How often should I descale my espresso machine?",
        checkpointer=InMemorySaver(),
    )
    assert result["citations"], "expected a grounded answer to trace"

    names = [span.name for span in langfuse.spans]
    for expected in ("agent.router", "agent.retrieve", "agent.answer", "agent.faithfulness_judge"):
        assert expected in names, names

    # Retrieval records what the model was allowed to see: chunk ids + scores.
    retrieved = langfuse.named("agent.retrieve").merged["output"]
    assert retrieved and all("chunk_id" in c and "score" in c for c in retrieved)

    # Generations carry token usage, so Langfuse can cost the turn.
    for span in (langfuse.named("llm.complete"), langfuse.named("llm.stream")):
        usage = span.merged["usage_details"]
        assert usage["input"] > 0 and usage["output"] > 0
        assert span.as_type == "generation"

    judge = langfuse.named("agent.faithfulness_judge")
    assert judge.as_type == "evaluator"
    assert "confidence" in judge.merged["output"]
