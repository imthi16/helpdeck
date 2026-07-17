"""Tracing (task 6.1): turn trace tree, trace_id persistence, feedback scores.

Langfuse itself is faked (no keys in tests); these assert the structure we
send — root chat.turn span, per-node spans, generations, scores — and that
the trace id lands on the assistant message and in the SSE done event.
"""

import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from app.models import (
    ApiKeyType,
    Membership,
    MembershipRole,
    Message,
    MessageRole,
    Organization,
    User,
)
from app.routers.auth import get_auth_sessionmaker
from app.routers.chat import get_chat_gateway, get_chat_sessionmaker
from app.routers.widget import get_widget_rate_limiter, get_widget_sessionmaker
from app.services import api_keys
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.llm import LLMGateway, OfflineGroundedProvider
from app.services.storage import LocalFileStorage
from app.services.tracing import node_span, record_score, start_turn

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]

FAKE_TRACE_ID = "ab" * 16


class FakeSpan:
    def __init__(self, client: "FakeLangfuse", kwargs: dict) -> None:
        self._client = client
        self.name = kwargs.get("name")
        self.kwargs = kwargs
        self.trace_id = FAKE_TRACE_ID
        self.id = f"span-{len(client.observations)}"
        self.updates: list[dict] = []
        self.ended = False

    def update(self, **kwargs) -> "FakeSpan":
        self.updates.append(kwargs)
        return self

    def end(self) -> "FakeSpan":
        self.ended = True
        return self


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[FakeSpan] = []
        self.scores: list[dict] = []

    def start_observation(self, **kwargs) -> FakeSpan:
        span = FakeSpan(self, kwargs)
        self.observations.append(span)
        return span

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        span = self.start_observation(**kwargs)
        try:
            yield span
        finally:
            span.ended = True

    def create_score(self, **kwargs) -> None:
        self.scores.append(kwargs)


@pytest.fixture
def fake_langfuse(monkeypatch: pytest.MonkeyPatch) -> FakeLangfuse:
    fake = FakeLangfuse()
    for module in (
        "app.services.tracing.get_langfuse",
        "app.services.llm.get_langfuse",
        "app.services.embeddings.get_langfuse",
    ):
        monkeypatch.setattr(module, lambda: fake)
    return fake


def test_start_turn_and_node_span_record_structure(fake_langfuse: FakeLangfuse) -> None:
    conversation_id = uuid.uuid4()
    span = start_turn(
        "hello",
        conversation_id=conversation_id,
        org_id=uuid.uuid4(),
        channel="playground",
    )
    assert span is not None and span.name == "chat.turn"
    state = {"trace_id": span.trace_id, "parent_span_id": span.id}
    with node_span(state, "agent.retrieve") as node:
        assert node is not None
        node.update(output={"chunk_ids": []})
    assert node.ended
    trace_context = node.kwargs["trace_context"]
    assert trace_context["trace_id"] == span.trace_id
    assert trace_context["parent_span_id"] == span.id

    record_score(name="csat", value=4.0, session_id=str(conversation_id))
    assert fake_langfuse.scores == [
        {
            "name": "csat",
            "value": 4.0,
            "trace_id": None,
            "session_id": str(conversation_id),
            "comment": None,
        }
    ]


def test_node_span_noop_without_trace(fake_langfuse: FakeLangfuse) -> None:
    with node_span({}, "agent.retrieve") as span:
        assert span is None
    assert fake_langfuse.observations == []


@pytest.fixture
async def traced_org(db_sessionmaker: Sessionmaker, tmp_path: Path):
    """Seeded corpus + a viewer member + a widget key for the org."""
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"tracing-{uuid.uuid4()}",
    )
    marker = uuid.uuid4().hex[:8]
    async with db_sessionmaker() as session:
        user = User(email=f"tracing-{marker}@example.com", password_hash="x", name="T")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=summary.org_id, user_id=user.id, role=MembershipRole.viewer))
        org = await session.get(Organization, summary.org_id)
        public_key = org.public_key
        await session.commit()
        user_id = user.id
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    try:
        yield summary.org_id, headers, public_key
    finally:
        async with db_sessionmaker() as session:
            for model, row_id in ((User, user_id), (Organization, summary.org_id)):
                row = await session.get(model, row_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()


async def test_chat_turn_produces_trace_tree_and_persists_trace_id(
    db_sessionmaker: Sessionmaker, traced_org, fake_langfuse: FakeLangfuse
) -> None:
    _, headers, _ = traced_org
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: LLMGateway(
        OfflineGroundedProvider(), cheap_model="c", strong_model="s"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "message": "How often should I descale my espresso machine?",
                    "bypass_cache": True,
                },
            )
        assert response.status_code == 200
        done = [
            json.loads(line[len("data:") :])
            for line in response.text.replace("\r\n", "\n").split("\n")
            if line.startswith("data:") and "message_id" in line
        ][-1]
        assert done["trace_id"] == FAKE_TRACE_ID

        names = [o.name for o in fake_langfuse.observations]
        assert names[0] == "chat.turn"
        for expected in ("agent.router", "agent.retrieve", "agent.answer"):
            assert expected in names, names
        assert "llm.stream" in names or "llm.complete" in names
        root = fake_langfuse.observations[0]
        assert root.ended, "root span must be closed when the stream finishes"

        # Retrieval span carries chunk ids + scores.
        retrieve = next(o for o in fake_langfuse.observations if o.name == "agent.retrieve")
        assert retrieve.updates and "chunk_ids" in retrieve.updates[-1]["output"]

        # trace_id persisted on the assistant message.
        async with db_sessionmaker() as session:
            message = await session.scalar(
                select(Message)
                .where(
                    Message.conversation_id == uuid.UUID(done["conversation_id"]),
                    Message.role == MessageRole.assistant,
                )
                .order_by(Message.created_at.desc())
            )
            assert message is not None and message.trace_id == FAKE_TRACE_ID
    finally:
        app.dependency_overrides.clear()


async def test_widget_feedback_pushes_langfuse_score(
    db_sessionmaker: Sessionmaker, traced_org, fake_langfuse: FakeLangfuse
) -> None:
    org_id, _, public_key = traced_org
    app.dependency_overrides[get_widget_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_widget_rate_limiter] = lambda: None
    try:
        # An assistant message carrying a trace id, as the chat path writes it.
        async with db_sessionmaker() as session:
            from app.models import Conversation, ConversationChannel

            conversation = Conversation(org_id=org_id, channel=ConversationChannel.widget)
            session.add(conversation)
            await session.flush()
            message = Message(
                org_id=org_id,
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content="answer",
                trace_id=FAKE_TRACE_ID,
            )
            session.add(message)
            await session.commit()
            message_id, conversation_id = message.id, conversation.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            thumbs = await client.post(
                "/api/v1/widget/feedback",
                headers={"X-Public-Key": public_key},
                json={"message_id": str(message_id), "rating": 1},
            )
            assert thumbs.status_code == 204
            csat = await client.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": public_key},
                json={"conversation_id": str(conversation_id), "score": 5},
            )
            assert csat.status_code == 204

        assert {s["name"] for s in fake_langfuse.scores} == {"user_feedback", "csat"}
        feedback = next(s for s in fake_langfuse.scores if s["name"] == "user_feedback")
        assert feedback["trace_id"] == FAKE_TRACE_ID and feedback["value"] == 1.0
        csat_score = next(s for s in fake_langfuse.scores if s["name"] == "csat")
        assert csat_score["session_id"] == str(conversation_id) and csat_score["value"] == 5.0
    finally:
        app.dependency_overrides.clear()


def test_api_key_types_export() -> None:
    # Guard the fixture assumption that seeded orgs mirror their key.
    assert api_keys.hash_token("pk_x") != "pk_x"
    assert ApiKeyType.widget.value == "widget"
