import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import (
    ApiKeyType,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Message,
    MessageRole,
    Organization,
)
from app.routers.widget import get_widget_rate_limiter, get_widget_sessionmaker
from app.services import api_keys
from app.services.cache import get_redis
from app.services.rate_limit import RateLimiter

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_widget_sessionmaker] = lambda: db_sessionmaker
    yield
    app.dependency_overrides.clear()


async def _make_org(sm: Sessionmaker, *, public_key: str, allowed_origins: str = "") -> uuid.UUID:
    async with sm() as session:
        org = Organization(
            name="Widget Org",
            public_key=public_key,
            widget_allowed_origins=allowed_origins,
            widget_welcome_message="Hey there!",
            widget_color="#123456",
        )
        session.add(org)
        await session.flush()
        # Widget auth reads api_keys since 5.3; mirror the org key there.
        widget_key, _ = api_keys.build_key(
            org_id=org.id, name="Widget key", key_type=ApiKeyType.widget, token=public_key
        )
        session.add(widget_key)
        await session.commit()
        return org.id


async def _cleanup(sm: Sessionmaker, org_id: uuid.UUID) -> None:
    async with sm() as session:
        org = await session.get(Organization, org_id)
        if org is not None:
            await session.delete(org)
            await session.commit()


def _client(client_ip: str = "testclient") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(client_ip, 12345)),
        base_url="http://test",
    )


async def test_config_requires_valid_key(db_sessionmaker: Sessionmaker) -> None:
    org_id = await _make_org(db_sessionmaker, public_key=f"pk_{uuid.uuid4().hex}")
    try:
        async with _client() as client:
            assert (await client.get("/api/v1/widget/config")).status_code == 401
            wrong = await client.get("/api/v1/widget/config", headers={"X-Public-Key": "pk_wrong"})
            assert wrong.status_code == 401
    finally:
        await _cleanup(db_sessionmaker, org_id)


async def test_config_ok_returns_branding(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_{uuid.uuid4().hex}"
    org_id = await _make_org(db_sessionmaker, public_key=key)
    try:
        async with _client() as client:
            resp = await client.get("/api/v1/widget/config", headers={"X-Public-Key": key})
            assert resp.status_code == 200
            body = resp.json()
            assert body["welcome_message"] == "Hey there!"
            assert body["color"] == "#123456"
    finally:
        await _cleanup(db_sessionmaker, org_id)


async def test_wrong_origin_forbidden(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_{uuid.uuid4().hex}"
    org_id = await _make_org(
        db_sessionmaker, public_key=key, allowed_origins="https://allowed.example"
    )
    try:
        async with _client() as client:
            blocked = await client.get(
                "/api/v1/widget/config",
                headers={"X-Public-Key": key, "Origin": "https://evil.example"},
            )
            assert blocked.status_code == 403

            allowed = await client.get(
                "/api/v1/widget/config",
                headers={"X-Public-Key": key, "Origin": "https://allowed.example"},
            )
            assert allowed.status_code == 200
    finally:
        await _cleanup(db_sessionmaker, org_id)


async def test_burst_hits_rate_limit(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_{uuid.uuid4().hex}"
    org_id = await _make_org(db_sessionmaker, public_key=key)
    redis_client = get_redis()
    app.dependency_overrides[get_widget_rate_limiter] = lambda: RateLimiter(
        redis_client, limit=3, window_seconds=60
    )
    # Unique client IP so the per-IP counter is isolated from other tests.
    unique_ip = f"10.0.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    try:
        async with _client(unique_ip) as client:
            statuses = [
                (
                    await client.get("/api/v1/widget/config", headers={"X-Public-Key": key})
                ).status_code
                for _ in range(5)
            ]
        assert statuses[:3] == [200, 200, 200]
        assert 429 in statuses[3:]

        # The 429 response carries Retry-After.
        async with _client(unique_ip) as client:
            limited = await client.get("/api/v1/widget/config", headers={"X-Public-Key": key})
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
    finally:
        app.dependency_overrides.pop(get_widget_rate_limiter, None)
        await redis_client.aclose()
        await _cleanup(db_sessionmaker, org_id)


async def test_feedback_records_thumbs(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_{uuid.uuid4().hex}"
    org_id = await _make_org(db_sessionmaker, public_key=key)
    try:
        async with db_sessionmaker() as session:
            conversation = Conversation(org_id=org_id, channel=ConversationChannel.widget)
            session.add(conversation)
            await session.flush()
            message = Message(
                org_id=org_id,
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content="Descale every three months [1].",
            )
            session.add(message)
            await session.commit()
            message_id = message.id

        async with _client() as client:
            resp = await client.post(
                "/api/v1/widget/feedback",
                headers={"X-Public-Key": key},
                json={"message_id": str(message_id), "rating": 1},
            )
            assert resp.status_code == 204

        async with db_sessionmaker() as session:
            refreshed = await session.get(Message, message_id)
            assert refreshed.feedback == 1
    finally:
        await _cleanup(db_sessionmaker, org_id)


async def test_csat_scores_once_and_closes_conversation(db_sessionmaker: Sessionmaker) -> None:
    key = f"pk_{uuid.uuid4().hex}"
    org_id = await _make_org(db_sessionmaker, public_key=key)
    async with db_sessionmaker() as session:
        conversation = Conversation(org_id=org_id, channel=ConversationChannel.widget)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    try:
        async with _client() as client:
            ok = await client.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={"conversation_id": str(conversation_id), "score": 4},
            )
            assert ok.status_code == 204

            again = await client.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={"conversation_id": str(conversation_id), "score": 5},
            )
            assert again.status_code == 409

            out_of_range = await client.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key},
                json={"conversation_id": str(conversation_id), "score": 6},
            )
            assert out_of_range.status_code == 422

        async with db_sessionmaker() as session:
            refreshed = await session.get(Conversation, conversation_id)
            assert refreshed.csat_score == 4
            assert refreshed.status == ConversationStatus.closed
    finally:
        await _cleanup(db_sessionmaker, org_id)


async def test_csat_rejects_other_orgs_conversation(db_sessionmaker: Sessionmaker) -> None:
    key_a = f"pk_{uuid.uuid4().hex}"
    org_a = await _make_org(db_sessionmaker, public_key=key_a)
    org_b = await _make_org(db_sessionmaker, public_key=f"pk_{uuid.uuid4().hex}")
    async with db_sessionmaker() as session:
        conversation = Conversation(org_id=org_b, channel=ConversationChannel.widget)
        session.add(conversation)
        await session.commit()
        conversation_id = conversation.id

    try:
        async with _client() as client:
            response = await client.post(
                "/api/v1/widget/csat",
                headers={"X-Public-Key": key_a},
                json={"conversation_id": str(conversation_id), "score": 5},
            )
        assert response.status_code == 404
    finally:
        await _cleanup(db_sessionmaker, org_a)
        await _cleanup(db_sessionmaker, org_b)
