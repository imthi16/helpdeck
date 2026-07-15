import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from app.models import Membership, MembershipRole, Message, MessageRole, Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.chat import get_chat_cache, get_chat_gateway, get_chat_sessionmaker
from app.services.cache import ResponseCache, get_redis
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.llm import LLMGateway, LLMUsage, OfflineGroundedProvider
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]


class CountingProvider(OfflineGroundedProvider):
    """Offline provider that counts how many times the model is invoked."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, model, messages, **kwargs) -> tuple[str, LLMUsage]:
        self.calls += 1
        text = self._respond(messages)
        return text, LLMUsage(prompt_tokens=1, completion_tokens=len(text.split()))

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        self.calls += 1
        for token in self._respond(messages).split(" "):
            yield token + " "


class SlowOfflineProvider(OfflineGroundedProvider):
    """Offline provider that streams answer tokens slowly for disconnect timing."""

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        for token in self._respond(messages).split(" "):
            await asyncio.sleep(self._delay)
            yield token + " "

    async def complete(self, model, messages, **kwargs) -> tuple[str, LLMUsage]:
        text = self._respond(messages)
        return text, LLMUsage(prompt_tokens=1, completion_tokens=len(text.split()))


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker, tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"chat-test-{uuid.uuid4()}",
    )
    try:
        yield summary.org_id
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


@pytest.fixture
async def auth_headers(db_sessionmaker: Sessionmaker, seeded: uuid.UUID):
    """An authenticated member of the seeded org (chat requires membership)."""
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    async with db_sessionmaker() as session:
        user = User(email=f"chat-{uuid.uuid4().hex[:10]}@example.com", password_hash="x", name="C")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=seeded, user_id=user.id, role=MembershipRole.viewer))
        await session.commit()
        user_id = user.id
    try:
        yield {"Authorization": f"Bearer {create_access_token(user_id)}"}
    finally:
        async with db_sessionmaker() as session:
            db_user = await session.get(User, user_id)
            if db_user is not None:
                await session.delete(db_user)
                await session.commit()


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    normalized = raw.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        data = None
        name = "message"
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if data is not None:
            try:
                events.append((name, json.loads(data)))
            except json.JSONDecodeError:
                pass
    return events


async def _messages(sm: Sessionmaker, conversation_id: uuid.UUID) -> list[Message]:
    async with sm() as session:
        return list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )


async def test_chat_streams_events_ending_in_done(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, auth_headers: dict
) -> None:
    org_id = seeded
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: LLMGateway(
        OfflineGroundedProvider(), cheap_model="c", strong_model="s"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=auth_headers
        ) as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "org_id": str(org_id),
                    "message": "How often should I descale my espresso machine?",
                },
            )
        assert response.status_code == 200
        events = _parse_sse(response.text)
        names = [name for name, _ in events]

        assert "status" in names
        assert "token" in names
        assert names[-1] == "done"
        done_payload = events[-1][1]
        assert done_payload["message_id"]
        assert done_payload["escalated"] is False

        # Full exchange persisted: user + assistant.
        conversation_id = uuid.UUID(done_payload["conversation_id"])
        messages = await _messages(db_sessionmaker, conversation_id)
        assert [m.role for m in messages] == [MessageRole.user, MessageRole.assistant]
        assert messages[1].citations
    finally:
        app.dependency_overrides.clear()


async def test_identical_query_served_from_cache_without_llm_call(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, auth_headers: dict
) -> None:
    org_id = seeded
    provider = CountingProvider()
    gateway = LLMGateway(provider, cheap_model="c", strong_model="s")
    redis_client = get_redis()
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: gateway
    app.dependency_overrides[get_chat_cache] = lambda: ResponseCache(redis_client, ttl_seconds=30)
    body = {
        "org_id": str(org_id),
        "message": "How often should I descale my espresso machine?",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=auth_headers
        ) as client:
            first = await client.post("/api/v1/chat", json=body)
            assert first.headers["X-HelpDeck-Cache"] == "miss"
            first_events = _parse_sse(first.text)
            assert first_events[-1][1]["cached"] is False
            calls_after_first = provider.calls
            assert calls_after_first > 0

            second = await client.post("/api/v1/chat", json=body)
            assert second.headers["X-HelpDeck-Cache"] == "hit"
            second_events = _parse_sse(second.text)
            assert second_events[-1][0] == "done"
            assert second_events[-1][1]["cached"] is True
            # No further LLM calls on the cached path.
            assert provider.calls == calls_after_first

            # Cache still returns the answer body and any citations.
            token_events = [p for name, p in second_events if name == "token"]
            assert token_events

            # Bypass flag forces a live run (and another LLM call).
            third = await client.post("/api/v1/chat", json={**body, "bypass_cache": True})
            assert third.headers["X-HelpDeck-Cache"] == "miss"
            assert provider.calls > calls_after_first
    finally:
        app.dependency_overrides.clear()
        await redis_client.aclose()


async def test_disconnect_midstream_leaves_no_assistant_message(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, auth_headers: dict
) -> None:
    org_id = seeded
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: LLMGateway(
        SlowOfflineProvider(delay=0.05), cheap_model="c", strong_model="s"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        conversation_id: uuid.UUID | None = None
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=auth_headers
        ) as client:
            async with client.stream(
                "POST",
                "/api/v1/chat",
                json={
                    "org_id": str(org_id),
                    "message": "How often should I descale my espresso machine?",
                },
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        payload = json.loads(line[len("data:") :].strip())
                        if "conversation_id" in payload:
                            conversation_id = uuid.UUID(payload["conversation_id"])
                        # Disconnect after the first streamed token.
                        if "text" in payload:
                            break
        # Give the server task a moment to observe the cancellation.
        await asyncio.sleep(0.2)

        # The user message may exist; the assistant message must not (no orphan).
        async with db_sessionmaker() as session:
            assistant = (
                (
                    await session.execute(
                        select(Message).where(Message.role == MessageRole.assistant)
                    )
                )
                .scalars()
                .all()
            )
            if conversation_id is not None:
                for message in assistant:
                    assert message.conversation_id != conversation_id
    finally:
        app.dependency_overrides.clear()
