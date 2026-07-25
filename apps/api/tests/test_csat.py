"""The 5.6 Verify: a widget rating persists and reaches analytics.

Also the guards: out-of-range scores are rejected, another org's conversation is
invisible, and a rating is write-once.
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.main import app
from app.models import (
    ApiKeyKind,
    Conversation,
    ConversationChannel,
    Message,
    MessageRole,
    Organization,
    User,
)
from app.routers.auth import get_auth_sessionmaker
from app.routers.deps import get_app_session_factory
from app.routers.widget import get_widget_rate_limiter, get_widget_sessionmaker
from app.services.api_keys import create_key

Sessionmaker = async_sessionmaker[AsyncSession]
PASSWORD = "hunter2pw"


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker) -> AsyncIterator[None]:
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_widget_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    app.dependency_overrides[get_widget_rate_limiter] = lambda: None
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _org_with_conversation(
    sm: Sessionmaker, *, public_key: str
) -> tuple[uuid.UUID, uuid.UUID]:
    async with sm() as session:
        org = Organization(name=f"csat-{uuid.uuid4().hex[:6]}")
        session.add(org)
        await session.flush()
        await create_key(
            session, org_id=org.id, kind=ApiKeyKind.public, name="Widget key", raw=public_key
        )
        conversation = Conversation(org_id=org.id, channel=ConversationChannel.widget)
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    org_id=org.id,
                    conversation_id=conversation.id,
                    role=MessageRole.user,
                    content="How do I descale?",
                ),
                Message(
                    org_id=org.id,
                    conversation_id=conversation.id,
                    role=MessageRole.assistant,
                    content="Every three months [1].",
                    confidence=0.9,
                ),
            ]
        )
        await session.commit()
        return org.id, conversation.id


async def _drop_org(sm: Sessionmaker, org_id: uuid.UUID) -> None:
    async with sm() as session:
        org = await session.get(Organization, org_id)
        if org is not None:
            await session.delete(org)
            await session.commit()


async def test_rating_persists_and_reaches_analytics(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_csat_{uuid.uuid4().hex[:12]}"
    org_id, conversation_id = await _org_with_conversation(db_sessionmaker, public_key=key)
    email = f"csat-owner-{uuid.uuid4().hex[:8]}@example.com"
    try:
        async with _client() as widget:
            response = await widget.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={"conversation_id": str(conversation_id), "score": 5},
            )
            assert response.status_code == 204, response.text

        async with db_sessionmaker() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation.csat_score == 5

        # The plan's "rating flows into analytics": an owner of that org sees it.
        async with db_sessionmaker() as session:
            from app.core.security import hash_password
            from app.models import Membership, MembershipRole

            user = User(email=email, password_hash=hash_password(PASSWORD), name="O")
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role=MembershipRole.owner))
            await session.commit()

        async with _client() as dashboard:
            assert (
                await dashboard.post("/auth/login", json={"email": email, "password": PASSWORD})
            ).status_code == 200
            overview = (await dashboard.get("/api/v1/analytics/overview")).json()
            assert overview["csat_average"] == 5.0
            assert overview["csat_responses"] == 1
    finally:
        async with db_sessionmaker() as session:
            user = await session.scalar(
                select(User).where(User.email == email).options(selectinload(User.memberships))
            )
            if user is not None:
                await session.delete(user)
                await session.commit()
        await _drop_org(db_sessionmaker, org_id)


@pytest.mark.parametrize("score", [0, 6, -1], ids=str)
async def test_out_of_range_scores_rejected(score: int, db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_csat_{uuid.uuid4().hex[:12]}"
    org_id, conversation_id = await _org_with_conversation(db_sessionmaker, public_key=key)
    try:
        async with _client() as widget:
            response = await widget.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={"conversation_id": str(conversation_id), "score": score},
            )
        assert response.status_code == 422
    finally:
        await _drop_org(db_sessionmaker, org_id)


async def test_second_rating_conflicts(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_csat_{uuid.uuid4().hex[:12]}"
    org_id, conversation_id = await _org_with_conversation(db_sessionmaker, public_key=key)
    try:
        async with _client() as widget:
            body = {"conversation_id": str(conversation_id), "score": 4}
            first = await widget.post(
                "/api/v1/widget/csat", headers={"X-Public-Key": key}, json=body
            )
            assert first.status_code == 204
            second = await widget.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={**body, "score": 1},
            )
            assert second.status_code == 409

        # The original score stands.
        async with db_sessionmaker() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation.csat_score == 4
    finally:
        await _drop_org(db_sessionmaker, org_id)


async def test_cannot_rate_another_orgs_conversation(db_sessionmaker: Sessionmaker) -> None:
    key_a = f"pk_csat_{uuid.uuid4().hex[:12]}"
    key_b = f"pk_csat_{uuid.uuid4().hex[:12]}"
    org_a, conversation_a = await _org_with_conversation(db_sessionmaker, public_key=key_a)
    org_b, _ = await _org_with_conversation(db_sessionmaker, public_key=key_b)
    try:
        async with _client() as widget:
            response = await widget.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key_b},
                json={"conversation_id": str(conversation_a), "score": 5},
            )
            assert response.status_code == 404

            # An unknown key gets nowhere either.
            assert (
                await widget.post(
                    "/api/v1/widget/csat",
                    headers={"X-Public-Key": "pk_not_a_key"},
                    json={"conversation_id": str(conversation_a), "score": 5},
                )
            ).status_code == 401

        async with db_sessionmaker() as session:
            conversation = await session.get(Conversation, conversation_a)
            assert conversation.csat_score is None
    finally:
        await _drop_org(db_sessionmaker, org_a)
        await _drop_org(db_sessionmaker, org_b)


async def test_thumbs_feedback_is_exposed_in_the_transcript(
    db_sessionmaker: Sessionmaker,
) -> None:
    """The inbox needs per-message feedback to show 👍/👎 (5.6)."""
    key = f"pk_csat_{uuid.uuid4().hex[:12]}"
    org_id, conversation_id = await _org_with_conversation(db_sessionmaker, public_key=key)
    email = f"inbox-{uuid.uuid4().hex[:8]}@example.com"
    try:
        async with db_sessionmaker() as session:
            message = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.role == MessageRole.assistant,
                )
            )
            message_id = message.id

        async with _client() as widget:
            assert (
                await widget.post(
                    "/api/v1/widget/feedback",
                    headers={"X-Public-Key": key},
                    json={"message_id": str(message_id), "rating": -1},
                )
            ).status_code == 204

        async with db_sessionmaker() as session:
            from app.core.security import hash_password
            from app.models import Membership, MembershipRole

            user = User(email=email, password_hash=hash_password(PASSWORD), name="I")
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role=MembershipRole.agent))
            await session.commit()

        async with _client() as dashboard:
            assert (
                await dashboard.post("/auth/login", json={"email": email, "password": PASSWORD})
            ).status_code == 200
            detail = (await dashboard.get(f"/api/v1/conversations/{conversation_id}")).json()
            assistant = [m for m in detail["messages"] if m["role"] == "assistant"][0]
            assert assistant["feedback"] == -1
    finally:
        async with db_sessionmaker() as session:
            user = await session.scalar(
                select(User).where(User.email == email).options(selectinload(User.memberships))
            )
            if user is not None:
                await session.delete(user)
                await session.commit()
        await _drop_org(db_sessionmaker, org_id)
