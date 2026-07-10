import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.main import app
from app.models import Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.onboarding import get_onboarding_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]


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


async def test_signup_starts_not_onboarded_then_completes(db_sessionmaker: Sessionmaker) -> None:
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_onboarding_sessionmaker] = lambda: db_sessionmaker
    email = f"onb-{uuid.uuid4().hex[:10]}@example.com"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            signup = await client.post(
                "/auth/signup",
                json={
                    "email": email,
                    "password": "hunter2pw",
                    "name": "O",
                    "org_name": "Temp Org",
                },
            )
            assert signup.json()["memberships"][0]["onboarded"] is False

            completed = await client.post(
                "/api/v1/onboarding/complete", json={"org_name": "Renamed Org"}
            )
            assert completed.status_code == 200
            membership = completed.json()["memberships"][0]
            assert membership["onboarded"] is True
            assert membership["org_name"] == "Renamed Org"

            me = await client.get("/auth/me")
            assert me.json()["memberships"][0]["onboarded"] is True
    finally:
        app.dependency_overrides.clear()
        await _cleanup(db_sessionmaker, email)
