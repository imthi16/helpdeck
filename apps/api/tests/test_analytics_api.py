"""The 5.5 Verify: seeded traffic produces exactly the expected numbers.

The fixture builds a known shape — 5 conversations, 2 escalated, 3 answered
cleanly, CSAT 4 and 5, one question asked twice — so every metric has one right
answer rather than "looks plausible".
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

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
from app.routers.deps import get_app_session_factory
from app.services.analytics import cluster_questions

Sessionmaker = async_sessionmaker[AsyncSession]
PASSWORD = "hunter2pw"

DUPLICATED_QUESTION = "Do you ship to Canada on weekends?"


@dataclass
class Seeded:
    org_id: uuid.UUID
    email: str
    client: httpx.AsyncClient


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker) -> AsyncIterator[None]:
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _add_conversation(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    question: str,
    answered: bool = True,
    escalated: bool = False,
    confidence: float | None = 0.9,
    csat: int | None = None,
    created_at: dt.datetime | None = None,
) -> uuid.UUID:
    conversation = Conversation(
        org_id=org_id,
        channel=ConversationChannel.widget,
        status=ConversationStatus.escalated if escalated else ConversationStatus.open,
        csat_score=csat,
    )
    if created_at is not None:
        conversation.created_at = created_at
    session.add(conversation)
    await session.flush()

    session.add(
        Message(
            org_id=org_id,
            conversation_id=conversation.id,
            role=MessageRole.user,
            content=question,
        )
    )
    if answered:
        session.add(
            Message(
                org_id=org_id,
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content="Here is an answer [1].",
                confidence=confidence,
            )
        )
    if escalated:
        session.add(
            Escalation(
                org_id=org_id, conversation_id=conversation.id, reason="no supporting context"
            )
        )
    return conversation.id


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker) -> AsyncIterator[Seeded]:
    """5 conversations: 3 clean answers (2 rated), 2 escalated (one question twice)."""
    email = f"analytics-{uuid.uuid4().hex[:8]}@example.com"
    client = _client()
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "name": "Owner", "org_name": "MetricsOrg"},
    )
    assert resp.status_code == 201, resp.text
    org_id = uuid.UUID(resp.json()["memberships"][0]["org_id"])

    async with db_sessionmaker() as session:
        await _add_conversation(session, org_id, question="Where is my order?", csat=4)
        await _add_conversation(session, org_id, question="How do I reset my password?", csat=5)
        await _add_conversation(session, org_id, question="What are your hours?")
        # Two escalations, both asking the same thing.
        await _add_conversation(session, org_id, question=DUPLICATED_QUESTION, escalated=True)
        await _add_conversation(session, org_id, question=DUPLICATED_QUESTION, escalated=True)
        # A thumbs pair on an answered turn.
        first = await session.scalar(
            select(Message).where(Message.org_id == org_id, Message.role == MessageRole.assistant)
        )
        first.feedback = 1
        await session.commit()

    try:
        yield Seeded(org_id=org_id, email=email, client=client)
    finally:
        await client.aclose()
        async with db_sessionmaker() as session:
            user = await session.scalar(
                select(User).where(User.email == email).options(selectinload(User.memberships))
            )
            if user is not None:
                await session.delete(user)
                await session.commit()
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def test_overview_numbers_are_exact(seeded: Seeded) -> None:
    response = await seeded.client.get("/api/v1/analytics/overview?days=30")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_conversations"] == 5
    assert body["escalated_conversations"] == 2
    assert body["deflected_conversations"] == 3
    assert body["escalation_rate"] == 0.4
    assert body["deflection_rate"] == 0.6
    assert body["csat_average"] == 4.5
    assert body["csat_responses"] == 2
    assert body["thumbs_up"] == 1
    assert body["thumbs_down"] == 0


async def test_conversations_over_time_is_zero_filled(seeded: Seeded) -> None:
    body = (await seeded.client.get("/api/v1/analytics/overview?days=7")).json()
    series = body["conversations_over_time"]

    # One point per day in the window, in order, no gaps.
    assert len(series) == 7
    days = [point["day"] for point in series]
    assert days == sorted(days)
    # Everything was seeded today.
    assert series[-1]["conversations"] == 5
    assert sum(point["conversations"] for point in series[:-1]) == 0


async def test_top_unanswered_clusters_the_repeated_question(seeded: Seeded) -> None:
    body = (await seeded.client.get("/api/v1/analytics/overview")).json()
    top = body["top_unanswered"]

    assert top, "expected the escalated questions to be listed"
    assert top[0]["question"] == DUPLICATED_QUESTION
    # Asked twice, reported once with a count of 2.
    assert top[0]["count"] == 2
    assert [c["question"] for c in top].count(DUPLICATED_QUESTION) == 1


async def test_analytics_are_org_scoped(seeded: Seeded, db_sessionmaker: Sessionmaker) -> None:
    other_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
    other = _client()
    try:
        resp = await other.post(
            "/auth/signup",
            json={
                "email": other_email,
                "password": PASSWORD,
                "name": "Other",
                "org_name": "QuietOrg",
            },
        )
        assert resp.status_code == 201
        body = (await other.get("/api/v1/analytics/overview")).json()
        # A fresh org sees its own emptiness, not the neighbour's traffic.
        assert body["total_conversations"] == 0
        assert body["csat_average"] is None
        assert body["top_unanswered"] == []
        assert body["deflection_rate"] == 0.0
    finally:
        await other.aclose()
        async with db_sessionmaker() as session:
            user = await session.scalar(
                select(User)
                .where(User.email == other_email)
                .options(selectinload(User.memberships))
            )
            if user is not None:
                org_ids = [m.org_id for m in user.memberships]
                await session.delete(user)
                await session.commit()
                for org_id in org_ids:
                    org = await session.get(Organization, org_id)
                    if org is not None:
                        await session.delete(org)
                await session.commit()


async def test_overview_requires_authentication() -> None:
    async with _client() as anon:
        assert (await anon.get("/api/v1/analytics/overview")).status_code == 401


async def test_days_parameter_is_bounded(seeded: Seeded) -> None:
    assert (await seeded.client.get("/api/v1/analytics/overview?days=0")).status_code == 422
    assert (await seeded.client.get("/api/v1/analytics/overview?days=400")).status_code == 422


def test_cluster_questions_groups_by_similarity() -> None:
    """Unit-level: identical vectors merge, distant ones don't, counts sort first."""
    questions = ["a", "a again", "b"]
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    clusters = cluster_questions(questions, vectors)

    assert [(c.question, c.count) for c in clusters] == [("a", 2), ("b", 1)]
    assert clusters[0].variants == ["a", "a again"]
