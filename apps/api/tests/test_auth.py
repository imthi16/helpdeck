import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import verify_password
from app.main import app
from app.models import Organization, User
from app.routers.auth import ACCESS_COOKIE, REFRESH_COOKIE, get_auth_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def override_sessionmaker(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    yield
    app.dependency_overrides.clear()


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _cleanup(sm: Sessionmaker, email: str) -> None:
    from sqlalchemy.orm import selectinload

    async with sm() as session:
        user = await session.scalar(
            select(User).where(User.email == email).options(selectinload(User.memberships))
        )
        if user is None:
            return
        org_ids = [m.org_id for m in user.memberships]
        await session.delete(user)  # cascades memberships
        await session.commit()
        for org_id in org_ids:
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
        await session.commit()


async def test_signup_creates_user_org_and_owner_membership(
    db_sessionmaker: Sessionmaker,
) -> None:
    email = unique_email()
    async with await _client() as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": email,
                "password": "hunter2pw",
                "name": "Ada",
                "org_name": "Ada's Coffee",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == email
        assert body["name"] == "Ada"
        assert len(body["memberships"]) == 1
        assert body["memberships"][0]["role"] == "owner"
        assert body["memberships"][0]["org_name"] == "Ada's Coffee"
        assert ACCESS_COOKIE in response.cookies
        assert REFRESH_COOKIE in response.cookies

        # Password is hashed, not stored in plaintext.
        async with db_sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None
            assert user.password_hash != "hunter2pw"
            assert verify_password("hunter2pw", user.password_hash)

        # Authenticated /auth/me works with the session cookie.
        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email

    await _cleanup(db_sessionmaker, email)


async def test_signup_duplicate_email_conflicts(db_sessionmaker: Sessionmaker) -> None:
    email = unique_email()
    payload = {
        "email": email,
        "password": "hunter2pw",
        "name": "A",
        "org_name": "Org",
    }
    async with await _client() as client:
        assert (await client.post("/auth/signup", json=payload)).status_code == 201
    async with await _client() as client2:
        dup = await client2.post("/auth/signup", json=payload)
        assert dup.status_code == 409
    await _cleanup(db_sessionmaker, email)


async def test_login_success_and_wrong_password(db_sessionmaker: Sessionmaker) -> None:
    email = unique_email()
    async with await _client() as client:
        await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "A", "org_name": "Org"},
        )

    async with await _client() as client:
        ok = await client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
        assert ok.status_code == 200
        assert ACCESS_COOKIE in ok.cookies

        bad = await client.post("/auth/login", json={"email": email, "password": "wrongpass"})
        assert bad.status_code == 401

    await _cleanup(db_sessionmaker, email)


async def test_refresh_issues_new_access_token(db_sessionmaker: Sessionmaker) -> None:
    email = unique_email()
    async with await _client() as client:
        await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "A", "org_name": "Org"},
        )
        # Drop the access cookie so /auth/me depends on refresh producing a new one.
        client.cookies.delete(ACCESS_COOKIE)

        refreshed = await client.post("/auth/refresh")
        assert refreshed.status_code == 200
        assert ACCESS_COOKIE in refreshed.cookies

        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email

    await _cleanup(db_sessionmaker, email)


async def test_me_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get("/auth/me")
        assert response.status_code == 401


async def test_expired_access_token_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": int((now - timedelta(minutes=30)).timestamp()),
            "exp": int((now - timedelta(minutes=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    async with await _client() as client:
        client.cookies.set(ACCESS_COOKIE, expired)
        response = await client.get("/auth/me")
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()


async def test_access_token_rejected_at_refresh_endpoint(db_sessionmaker: Sessionmaker) -> None:
    email = unique_email()
    async with await _client() as client:
        await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "A", "org_name": "Org"},
        )
        access = client.cookies.get(ACCESS_COOKIE)
        # Present an access token where a refresh token is required.
        client.cookies.delete(REFRESH_COOKIE)
        client.cookies.set(REFRESH_COOKIE, access, path="/auth/refresh")
        response = await client.post("/auth/refresh")
        assert response.status_code == 401
    await _cleanup(db_sessionmaker, email)
