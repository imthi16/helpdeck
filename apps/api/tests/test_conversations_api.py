import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.main import app
from app.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Escalation,
    Message,
    MessageRole,
    Organization,
    User,
)
from app.routers.auth import get_auth_sessionmaker
from app.routers.conversations import get_conversations_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_conversations_sessionmaker] = lambda: db_sessionmaker
    yield
    app.dependency_overrides.clear()


async def _signed_in() -> tuple[httpx.AsyncClient, str, uuid.UUID]:
    email = f"conv-{uuid.uuid4().hex[:10]}@example.com"
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "hunter2pw", "name": "C", "org_name": "ConvOrg"},
    )
    org_id = uuid.UUID(resp.json()["memberships"][0]["org_id"])
    return client, email, org_id


async def _seed_escalated(sm: Sessionmaker, org_id: uuid.UUID) -> uuid.UUID:
    async with sm() as session:
        conversation = Conversation(
            org_id=org_id,
            channel=ConversationChannel.playground,
            status=ConversationStatus.escalated,
        )
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    org_id=org_id,
                    conversation_id=conversation.id,
                    role=MessageRole.user,
                    content="What is your CEO's shoe size?",
                ),
                Message(
                    org_id=org_id,
                    conversation_id=conversation.id,
                    role=MessageRole.assistant,
                    content="I don't have enough information to answer that.",
                ),
                Escalation(
                    org_id=org_id,
                    conversation_id=conversation.id,
                    reason="no supporting context found",
                ),
            ]
        )
        await session.commit()
        return conversation.id


async def _cleanup(sm: Sessionmaker, email: str) -> None:
    async with sm() as session:
        user = await session.scalar(
            select(User).where(User.email == email).options(selectinload(User.memberships))
        )
        if user is None:
            return
        org_ids = [m.org_id for m in user.memberships]
        await session.delete(user)
        await session.commit()
        for org_id in org_ids:
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
        await session.commit()


async def test_escalated_conversation_appears_and_resolves(db_sessionmaker: Sessionmaker) -> None:
    client, email, org_id = await _signed_in()
    try:
        conversation_id = await _seed_escalated(db_sessionmaker, org_id)

        # Appears in the escalated filter.
        escalated = await client.get("/api/v1/conversations", params={"status": "escalated"})
        assert escalated.status_code == 200
        ids = [c["id"] for c in escalated.json()]
        assert str(conversation_id) in ids
        summary = next(c for c in escalated.json() if c["id"] == str(conversation_id))
        assert summary["message_count"] == 2

        # Transcript includes messages and the escalation.
        detail = await client.get(f"/api/v1/conversations/{conversation_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert len(body["messages"]) == 2
        assert len(body["escalations"]) == 1
        assert body["escalations"][0]["status"] == "pending"

        # Resolving closes the conversation and resolves the escalation.
        resolved = await client.post(f"/api/v1/conversations/{conversation_id}/resolve")
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "closed"
        assert resolved.json()["escalations"][0]["status"] == "resolved"
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_internal_reply_appended_to_transcript(db_sessionmaker: Sessionmaker) -> None:
    client, email, org_id = await _signed_in()
    try:
        conversation_id = await _seed_escalated(db_sessionmaker, org_id)
        reply = await client.post(
            f"/api/v1/conversations/{conversation_id}/reply",
            json={"content": "Our CEO wears size 10. Thanks for reaching out!"},
        )
        assert reply.status_code == 200
        contents = [m["content"] for m in reply.json()["messages"]]
        assert "Our CEO wears size 10. Thanks for reaching out!" in contents
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_conversations_require_auth() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/v1/conversations")).status_code == 401


async def test_cannot_read_other_orgs_conversation(db_sessionmaker: Sessionmaker) -> None:
    client_a, email_a, org_a = await _signed_in()
    client_b, email_b, _ = await _signed_in()
    try:
        conversation_id = await _seed_escalated(db_sessionmaker, org_a)
        assert (await client_b.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    finally:
        await client_a.aclose()
        await client_b.aclose()
        await _cleanup(db_sessionmaker, email_a)
        await _cleanup(db_sessionmaker, email_b)
