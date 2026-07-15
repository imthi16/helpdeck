"""Analytics overview (task 5.5): numbers asserted against seeded fixtures."""

import datetime
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from app.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Membership,
    MembershipRole,
    Message,
    MessageRole,
    Organization,
    User,
)
from app.routers.analytics import (
    get_analytics_embedding_service,
    get_analytics_redis,
    get_analytics_sessionmaker,
)
from app.routers.auth import get_auth_sessionmaker
from app.services.analytics import UnansweredCluster, cluster_questions
from app.services.embeddings import EmbeddingService

Sessionmaker = async_sessionmaker[AsyncSession]


class DirectionalFakeProvider:
    """Embeds by keyword so similar questions land in the same cluster."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    1.0 if "refund" in lowered else 0.0,
                    1.0 if "shipping" in lowered else 0.0,
                    1.0 if "warranty" in lowered else 0.0,
                ]
            )
        return vectors


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_analytics_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_analytics_embedding_service] = lambda: EmbeddingService(
        DirectionalFakeProvider(), model="t"
    )
    app.dependency_overrides[get_analytics_redis] = lambda: None  # no cache in tests
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_traffic(db_sessionmaker: Sessionmaker):
    """Org with 6 conversations: 2 escalated, 3 answered+resolved, 1 empty.

    CSAT on two conversations (4 and 5 -> avg 4.5). Low-confidence answer plus
    escalations feed 'top unanswered' (2 refund questions, 1 shipping).
    """
    marker = uuid.uuid4().hex[:8]
    now = datetime.datetime.now(datetime.UTC)
    async with db_sessionmaker() as session:
        org = Organization(name=f"analytics-{marker}")
        session.add(org)
        await session.flush()
        org_id = org.id
        user = User(email=f"analytics-{marker}@example.com", password_hash="x", name="A")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org_id, user_id=user.id, role=MembershipRole.viewer))

        def conversation(status: ConversationStatus, csat: int | None = None) -> Conversation:
            return Conversation(
                org_id=org_id,
                channel=ConversationChannel.widget,
                status=status,
                csat_score=csat,
            )

        # 2 escalated conversations, each with a refund question -> same cluster.
        escalated = [conversation(ConversationStatus.escalated) for _ in range(2)]
        # 3 answered, non-escalated (deflected); one carries a low-confidence
        # shipping answer that also counts as unanswered.
        resolved = [
            conversation(ConversationStatus.closed, csat=4),
            conversation(ConversationStatus.open, csat=5),
            conversation(ConversationStatus.open),
        ]
        empty = conversation(ConversationStatus.open)
        session.add_all([*escalated, *resolved, empty])
        await session.flush()

        # Explicit, increasing created_at: in production user/assistant rows
        # commit in separate transactions so their timestamps differ, but a
        # single-transaction fixture would give every row the same now().
        clock = {"tick": 0}

        def message(conv: Conversation, role: MessageRole, content: str, **kw) -> Message:
            clock["tick"] += 1
            return Message(
                org_id=org_id,
                conversation_id=conv.id,
                role=role,
                content=content,
                created_at=now - datetime.timedelta(minutes=60 - clock["tick"]),
                **kw,
            )

        session.add_all(
            [
                message(escalated[0], MessageRole.user, "Can I get a refund for my order?"),
                message(escalated[0], MessageRole.assistant, "handoff", confidence=0.1),
                message(escalated[1], MessageRole.user, "How do refunds work exactly?"),
                message(escalated[1], MessageRole.assistant, "handoff", confidence=0.0),
                message(resolved[0], MessageRole.user, "What are the shipping times?"),
                message(resolved[0], MessageRole.assistant, "low conf", confidence=0.1),
                message(resolved[1], MessageRole.user, "Descaling?"),
                message(resolved[1], MessageRole.assistant, "answered", confidence=0.9),
                message(resolved[2], MessageRole.user, "Warranty length?"),
                message(resolved[2], MessageRole.assistant, "answered", confidence=0.95),
            ]
        )
        await session.commit()
        user_id = user.id

    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    try:
        yield org_id, headers, now
    finally:
        async with db_sessionmaker() as session:
            for model, row_id in ((User, user_id), (Organization, org_id)):
                row = await session.get(model, row_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()


async def test_overview_numbers_match_fixtures(seeded_traffic) -> None:
    _, headers, _ = seeded_traffic
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/analytics/overview?days=7", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total_conversations"] == 6
    assert data["escalated_conversations"] == 2
    # 5 conversations have an assistant message (2 escalated + 3 resolved).
    assert data["answered_conversations"] == 5
    assert data["escalation_rate"] == pytest.approx(2 / 6)
    # Deflected = answered and not escalated = 3 of 5 answered.
    assert data["deflection_rate"] == pytest.approx(3 / 5)
    assert data["csat_average"] == pytest.approx(4.5)
    assert data["csat_responses"] == 2

    # Conversations/day zero-fills and puts all 6 on today.
    per_day = data["conversations_per_day"]
    assert len(per_day) == 8  # 7 days back + today
    assert per_day[-1]["count"] == 6
    assert all(entry["count"] == 0 for entry in per_day[:-1])

    # Unanswered clustering: 2 refund questions group; shipping is separate;
    # the confident warranty answer must NOT appear.
    clusters = {c["question"]: c["count"] for c in data["top_unanswered"]}
    assert clusters.get("How do refunds work exactly?", 0) == 2 or any(
        "refund" in q.lower() and n == 2 for q, n in clusters.items()
    )
    assert any("shipping" in q.lower() and n == 1 for q, n in clusters.items())
    assert not any("warranty" in q.lower() for q in clusters)


def test_cluster_questions_groups_by_similarity() -> None:
    questions = [
        ("Can I get a refund for my order?", "2026-07-15T00:00:02"),
        ("How do refunds work?", "2026-07-15T00:00:01"),
        ("What are the shipping times?", "2026-07-15T00:00:00"),
    ]
    vectors = [[1.0, 0.0], [1.0, 0.02], [0.0, 1.0]]
    clusters = cluster_questions(questions, vectors)
    assert clusters[0] == UnansweredCluster(
        question="How do refunds work?", count=2, last_seen="2026-07-15T00:00:02"
    )
    assert clusters[1].count == 1


async def test_quality_endpoint_returns_latest_eval_run(
    seeded_traffic, db_sessionmaker: Sessionmaker
) -> None:
    from app.models import EvalRun

    _, headers, _ = seeded_traffic
    async with db_sessionmaker() as session:
        session.add(
            EvalRun(
                kind="ci",
                dataset="golden:fast",
                item_count=30,
                metrics={"context_recall": 0.92, "citation_validity": 1.0},
                thresholds={"context_recall": 0.7},
                passed=True,
            )
        )
        await session.commit()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/analytics/quality", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["latest"]["kind"] == "ci"
        assert payload["latest"]["metrics"]["context_recall"] == pytest.approx(0.92)
        assert payload["trend"], "trend should include the seeded run"
    finally:
        async with db_sessionmaker() as session:
            from sqlalchemy import delete

            await session.execute(delete(EvalRun).where(EvalRun.kind == "ci"))
            await session.commit()
