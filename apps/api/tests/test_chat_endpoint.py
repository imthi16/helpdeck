import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import Message, MessageRole, Organization
from app.routers.chat import get_chat_gateway, get_chat_sessionmaker
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.llm import LLMGateway, LLMUsage, OfflineGroundedProvider
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]


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
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: LLMGateway(
        OfflineGroundedProvider(), cheap_model="c", strong_model="s"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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


async def test_disconnect_midstream_leaves_no_assistant_message(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_gateway] = lambda: LLMGateway(
        SlowOfflineProvider(delay=0.05), cheap_model="c", strong_model="s"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        conversation_id: uuid.UUID | None = None
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
