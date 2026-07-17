"""API keys (task 5.3): owner CRUD, reveal-once secrets, immediate revocation."""

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from app.models import ApiKey, Membership, MembershipRole, Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.keys import get_keys_sessionmaker
from app.routers.widget import get_widget_rate_limiter, get_widget_sessionmaker
from app.services import api_keys

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_keys_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_widget_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_widget_rate_limiter] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def org_users(db_sessionmaker: Sessionmaker):
    """(org_id, owner_headers, admin_headers) plus cleanup."""
    marker = uuid.uuid4().hex[:8]
    user_ids: list[uuid.UUID] = []
    headers: dict[str, dict[str, str]] = {}
    async with db_sessionmaker() as session:
        org = Organization(name=f"keys-{marker}")
        session.add(org)
        await session.flush()
        org_id = org.id
        for role in (MembershipRole.owner, MembershipRole.admin):
            user = User(
                email=f"keys-{role.value}-{marker}@example.com", password_hash="x", name="K"
            )
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role=role))
            user_ids.append(user.id)
            headers[role.value] = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        await session.commit()
    try:
        yield org_id, headers["owner"], headers["admin"]
    finally:
        async with db_sessionmaker() as session:
            for user_id in user_ids:
                user = await session.get(User, user_id)
                if user is not None:
                    await session.delete(user)
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
            await session.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_keys_are_owner_only(org_users) -> None:
    _, _, admin_headers = org_users
    async with _client() as client:
        assert (await client.get("/api/v1/keys", headers=admin_headers)).status_code == 403
        created = await client.post(
            "/api/v1/keys", json={"name": "ci", "key_type": "secret"}, headers=admin_headers
        )
        assert created.status_code == 403


async def test_secret_key_revealed_once_widget_key_redisplayable(org_users) -> None:
    _, owner_headers, _ = org_users
    async with _client() as client:
        secret = await client.post(
            "/api/v1/keys", json={"name": "server", "key_type": "secret"}, headers=owner_headers
        )
        assert secret.status_code == 201
        payload = secret.json()
        assert payload["token"].startswith("sk_")
        assert payload["public_value"] is None  # never re-displayable

        widget = await client.post(
            "/api/v1/keys", json={"name": "site", "key_type": "widget"}, headers=owner_headers
        )
        assert widget.status_code == 201
        assert widget.json()["token"].startswith("pk_")
        assert widget.json()["public_value"] == widget.json()["token"]

        listing = await client.get("/api/v1/keys", headers=owner_headers)
        assert listing.status_code == 200
        by_id = {k["id"]: k for k in listing.json()}
        assert "token" not in by_id[payload["id"]]
        assert by_id[payload["id"]]["public_value"] is None
        assert by_id[widget.json()["id"]]["public_value"] == widget.json()["token"]


async def test_revoked_widget_key_401s_immediately_and_last_used_updates(
    org_users, db_sessionmaker: Sessionmaker
) -> None:
    org_id, owner_headers, _ = org_users
    async with _client() as client:
        created = await client.post(
            "/api/v1/keys", json={"name": "site", "key_type": "widget"}, headers=owner_headers
        )
        token = created.json()["token"]

        ok = await client.get("/api/v1/widget/config", headers={"X-Public-Key": token})
        assert ok.status_code == 200

        # last_used_at was stamped by the successful widget call.
        async with db_sessionmaker() as session:
            key = await session.get(ApiKey, uuid.UUID(created.json()["id"]))
            assert key is not None and key.last_used_at is not None

        revoked = await client.delete(f"/api/v1/keys/{created.json()['id']}", headers=owner_headers)
        assert revoked.status_code == 200
        assert revoked.json()["revoked_at"] is not None

        denied = await client.get("/api/v1/widget/config", headers={"X-Public-Key": token})
        assert denied.status_code == 401


async def test_signup_creates_matching_widget_key(db_sessionmaker: Sessionmaker) -> None:
    email = f"keys-signup-{uuid.uuid4().hex[:8]}@example.com"
    async with _client() as client:
        signup = await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "S", "org_name": "KeyOrg"},
        )
        assert signup.status_code == 201
        org_id = signup.json()["memberships"][0]["org_id"]

    async with db_sessionmaker() as session:
        from sqlalchemy import select

        org = await session.get(Organization, uuid.UUID(org_id))
        assert org is not None
        key = await session.scalar(
            select(ApiKey).where(ApiKey.org_id == org.id, ApiKey.key_type == "widget")
        )
        assert key is not None
        assert key.public_value == org.public_key
        assert key.secret_hash == api_keys.hash_token(org.public_key)

        user = await session.scalar(select(User).where(User.email == email))
        if user is not None:
            await session.delete(user)
        await session.delete(org)
        await session.commit()
