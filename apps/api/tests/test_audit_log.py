"""Audit log (task 5.4): actions produce rows; the table is append-only."""

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app
from app.models import Membership, MembershipRole, Organization, User
from app.routers.audit import get_audit_sessionmaker
from app.routers.auth import get_auth_sessionmaker
from app.routers.keys import get_keys_sessionmaker
from app.routers.members import get_members_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_keys_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_members_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_audit_sessionmaker] = lambda: db_sessionmaker
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def owner_org(db_sessionmaker: Sessionmaker):
    marker = uuid.uuid4().hex[:8]
    async with db_sessionmaker() as session:
        org = Organization(name=f"audit-{marker}")
        session.add(org)
        await session.flush()
        user = User(email=f"audit-{marker}@example.com", password_hash="x", name="A")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=MembershipRole.owner))
        await session.commit()
        org_id, user_id = org.id, user.id
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    try:
        yield org_id, user_id, headers
    finally:
        async with db_sessionmaker() as session:
            for model, row_id in ((User, user_id), (Organization, org_id)):
                row = await session.get(model, row_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _actions(sm: Sessionmaker, org_id: uuid.UUID) -> list[str]:
    async with sm() as session:
        rows = await session.execute(
            text("SELECT action FROM audit_logs WHERE org_id = :org ORDER BY id"),
            {"org": str(org_id)},
        )
        return [row.action for row in rows]


async def test_actions_produce_audit_rows(owner_org, db_sessionmaker: Sessionmaker) -> None:
    org_id, _, headers = owner_org
    async with _client() as client:
        created = await client.post(
            "/api/v1/keys", json={"name": "audited", "key_type": "secret"}, headers=headers
        )
        assert created.status_code == 201
        revoked = await client.delete(f"/api/v1/keys/{created.json()['id']}", headers=headers)
        assert revoked.status_code == 200
        invite = await client.post(
            "/api/v1/members/invites",
            json={"email": "aud@example.com", "role": "viewer"},
            headers=headers,
        )
        assert invite.status_code == 201

        actions = await _actions(db_sessionmaker, org_id)
        assert actions == ["key.created", "key.revoked", "member.invited"]

        # Admin+ can read the org's rows through the viewer endpoint.
        listing = await client.get("/api/v1/audit-logs", headers=headers)
        assert listing.status_code == 200
        assert [e["action"] for e in listing.json()] == [
            "member.invited",
            "key.revoked",
            "key.created",
        ]
        # Payloads are PII-free: the invite is referenced by id, never email.
        assert listing.json()[0]["payload"] == {"role": "viewer"}
        assert "email" not in listing.json()[0]["payload"]


async def test_login_and_signup_are_audited(db_sessionmaker: Sessionmaker) -> None:
    email = f"audit-auth-{uuid.uuid4().hex[:8]}@example.com"
    async with _client() as client:
        signup = await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "A", "org_name": "AuditOrg"},
        )
        assert signup.status_code == 201
        org_id = uuid.UUID(signup.json()["memberships"][0]["org_id"])
        login = await client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
        assert login.status_code == 200

    try:
        actions = await _actions(db_sessionmaker, org_id)
        assert actions == ["auth.signup", "auth.login"]
    finally:
        async with db_sessionmaker() as session:
            from sqlalchemy import select

            user = await session.scalar(select(User).where(User.email == email))
            org = await session.get(Organization, org_id)
            if user is not None:
                await session.delete(user)
            if org is not None:
                await session.delete(org)
            await session.commit()


async def test_audit_rows_are_append_only(
    owner_org, db_sessionmaker: Sessionmaker, app_login: None
) -> None:
    org_id, _, headers = owner_org
    async with _client() as client:
        assert (
            await client.post(
                "/api/v1/keys", json={"name": "x", "key_type": "secret"}, headers=headers
            )
        ).status_code == 201

    # Even the superuser (table owner) cannot mutate rows: trigger raises.
    async with db_sessionmaker() as session:
        with pytest.raises((ProgrammingError, DBAPIError)):
            await session.execute(
                text("UPDATE audit_logs SET action = 'tampered' WHERE org_id = :org"),
                {"org": str(org_id)},
            )
        await session.rollback()
        with pytest.raises((ProgrammingError, DBAPIError)):
            await session.execute(
                text("DELETE FROM audit_logs WHERE org_id = :org"), {"org": str(org_id)}
            )
        await session.rollback()

    # The app role cannot INSERT directly (no privilege, no INSERT policy).
    engine = create_async_engine(get_settings().app_database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(
                        "INSERT INTO audit_logs (org_id, actor_type, action)"
                        " VALUES (:org, 'user', 'forged')"
                    ),
                    {"org": str(org_id)},
                )
            await session.rollback()
    finally:
        await engine.dispose()


async def test_audit_viewer_requires_admin(owner_org, db_sessionmaker: Sessionmaker) -> None:
    org_id, _, _ = owner_org
    marker = uuid.uuid4().hex[:8]
    async with db_sessionmaker() as session:
        viewer = User(email=f"audit-viewer-{marker}@example.com", password_hash="x", name="V")
        session.add(viewer)
        await session.flush()
        session.add(Membership(org_id=org_id, user_id=viewer.id, role=MembershipRole.viewer))
        await session.commit()
        viewer_id = viewer.id

    try:
        async with _client() as client:
            response = await client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": f"Bearer {create_access_token(viewer_id)}"},
            )
        assert response.status_code == 403
    finally:
        async with db_sessionmaker() as session:
            user = await session.get(User, viewer_id)
            if user is not None:
                await session.delete(user)
                await session.commit()
