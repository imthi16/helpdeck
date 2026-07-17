"""RBAC matrix (task 5.2): each role against each sensitive endpoint.

One org, four users (owner/admin/agent/viewer). Disallowed roles must get 403
from the role gate; allowed roles must get past it (2xx, or 404 when the
target row deliberately doesn't exist — proving the gate, not the handler).
Also covers the invite flow (token link, signup branch) and its guards.
"""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from app.models import Membership, MembershipRole, Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.chat import get_chat_sessionmaker
from app.routers.conversations import get_conversations_sessionmaker
from app.routers.documents import (
    get_documents_sessionmaker,
    get_documents_storage,
    get_ingest_queue,
)
from app.routers.members import get_members_sessionmaker
from app.routers.onboarding import get_onboarding_sessionmaker
from app.services.storage import LocalFileStorage

Sessionmaker = async_sessionmaker[AsyncSession]
ROLES = [
    MembershipRole.owner,
    MembershipRole.admin,
    MembershipRole.agent,
    MembershipRole.viewer,
]


class NullQueue:
    async def enqueue_ingest(self, document_id: uuid.UUID, org_id: uuid.UUID) -> None:
        return None


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker, tmp_path: Path):
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_members_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_conversations_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_onboarding_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_chat_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_storage] = lambda: LocalFileStorage(tmp_path)
    app.dependency_overrides[get_ingest_queue] = lambda: NullQueue()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def org_with_roles(db_sessionmaker: Sessionmaker):
    """One org, one user per role. Yields (org_id, {role: (user_id, headers)})."""
    marker = uuid.uuid4().hex[:8]
    users: dict[MembershipRole, tuple[uuid.UUID, dict[str, str]]] = {}
    async with db_sessionmaker() as session:
        org = Organization(name=f"rbac-{marker}")
        session.add(org)
        await session.flush()
        org_id = org.id
        for role in ROLES:
            user = User(
                email=f"rbac-{role.value}-{marker}@example.com",
                password_hash="x",
                name=role.value,
            )
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role=role))
            users[role] = (
                user.id,
                {"Authorization": f"Bearer {create_access_token(user.id)}"},
            )
        await session.commit()
    try:
        yield org_id, users
    finally:
        async with db_sessionmaker() as session:
            for user_id, _ in users.values():
                db_user = await session.get(User, user_id)
                if db_user is not None:
                    await session.delete(db_user)
            db_org = await session.get(Organization, org_id)
            if db_org is not None:
                await session.delete(db_org)
            await session.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# (method, path, body, minimum role). Paths with {agent} target the agent user.
MATRIX = [
    ("GET", "/api/v1/documents", None, MembershipRole.viewer),
    ("GET", "/api/v1/members", None, MembershipRole.viewer),
    ("GET", "/api/v1/conversations", None, MembershipRole.viewer),
    (
        "POST",
        f"/api/v1/conversations/{uuid.uuid4()}/resolve",
        None,
        MembershipRole.agent,
    ),
    (
        "POST",
        f"/api/v1/conversations/{uuid.uuid4()}/reply",
        {"content": "hi"},
        MembershipRole.agent,
    ),
    (
        "POST",
        "/api/v1/documents",
        {"source_type": "text", "title": "t", "content": "hello world"},
        MembershipRole.admin,
    ),
    ("DELETE", f"/api/v1/documents/{uuid.uuid4()}", None, MembershipRole.admin),
    ("POST", "/api/v1/onboarding/complete", {}, MembershipRole.admin),
    (
        "POST",
        "/api/v1/members/invites",
        {"email": "new@example.com", "role": "viewer"},
        MembershipRole.admin,
    ),
    ("GET", "/api/v1/members/invites", None, MembershipRole.admin),
    ("PATCH", "/members/{agent}", {"role": "viewer"}, MembershipRole.admin),
]

_RANK = {r: i for i, r in enumerate(reversed(ROLES))}


@pytest.mark.parametrize("role", ROLES)
async def test_role_matrix(role: MembershipRole, org_with_roles) -> None:
    org_id, users = org_with_roles
    agent_id = users[MembershipRole.agent][0]
    _, headers = users[role]

    async with _client() as client:
        for method, path, body, minimum in MATRIX:
            if path == "/members/{agent}":
                path = f"/api/v1/members/{agent_id}"
            allowed = _RANK[role] >= _RANK[minimum]
            response = await client.request(method, path, json=body, headers=headers)
            if allowed:
                assert response.status_code != 403, (
                    f"{role.value} unexpectedly forbidden on {method} {path}: "
                    f"{response.status_code} {response.text}"
                )
                assert response.status_code < 500
            else:
                assert response.status_code == 403, (
                    f"{role.value} should be forbidden on {method} {path}, "
                    f"got {response.status_code}"
                )


async def test_x_org_id_must_match_a_membership(org_with_roles) -> None:
    _, users = org_with_roles
    _, headers = users[MembershipRole.owner]
    async with _client() as client:
        response = await client.get(
            "/api/v1/documents", headers={**headers, "X-Org-Id": str(uuid.uuid4())}
        )
    assert response.status_code == 403


async def test_invite_flow_signup_joins_at_invited_role(
    org_with_roles, db_sessionmaker: Sessionmaker
) -> None:
    org_id, users = org_with_roles
    _, owner_headers = users[MembershipRole.owner]
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"

    async with _client() as client:
        created = await client.post(
            "/api/v1/members/invites",
            json={"email": email, "role": "agent"},
            headers=owner_headers,
        )
        assert created.status_code == 201
        payload = created.json()
        token = payload["invite_url"].rsplit("/", 1)[-1]
        assert token and "invite_url" in payload

        # Invitee signs up with the token: no org created, joins at agent role.
        signup = await client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pw", "name": "I", "invite_token": token},
        )
        assert signup.status_code == 201
        memberships = signup.json()["memberships"]
        assert len(memberships) == 1
        assert memberships[0]["org_id"] == str(org_id)
        assert memberships[0]["role"] == "agent"

        # Token is single-use.
        again = await client.post(
            "/auth/signup",
            json={
                "email": f"other-{uuid.uuid4().hex[:6]}@example.com",
                "password": "hunter2pw",
                "name": "O",
                "invite_token": token,
            },
        )
        assert again.status_code == 400

    async with db_sessionmaker() as session:
        from sqlalchemy import select

        user = await session.scalar(select(User).where(User.email == email))
        if user is not None:
            await session.delete(user)
            await session.commit()


async def test_signup_requires_org_name_or_invite() -> None:
    async with _client() as client:
        response = await client.post(
            "/auth/signup",
            json={"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "password": "hunter2pw"},
        )
    assert response.status_code == 422


async def test_last_owner_cannot_be_demoted_or_removed(org_with_roles) -> None:
    _, users = org_with_roles
    owner_id, owner_headers = users[MembershipRole.owner]
    async with _client() as client:
        demote = await client.patch(
            f"/api/v1/members/{owner_id}", json={"role": "admin"}, headers=owner_headers
        )
        assert demote.status_code == 409
        remove = await client.delete(f"/api/v1/members/{owner_id}", headers=owner_headers)
        assert remove.status_code == 409


async def test_admin_cannot_manage_admins_or_owners(org_with_roles) -> None:
    _, users = org_with_roles
    owner_id, _ = users[MembershipRole.owner]
    _, admin_headers = users[MembershipRole.admin]
    async with _client() as client:
        # An admin may not touch the owner...
        response = await client.patch(
            f"/api/v1/members/{owner_id}", json={"role": "viewer"}, headers=admin_headers
        )
        assert response.status_code == 403
        # ...nor grant a role at their own rank or above.
        invite = await client.post(
            "/api/v1/members/invites",
            json={"email": "esc@example.com", "role": "owner"},
            headers=admin_headers,
        )
        assert invite.status_code == 403


async def test_member_remove_and_role_change(org_with_roles, db_sessionmaker) -> None:
    org_id, users = org_with_roles
    _, owner_headers = users[MembershipRole.owner]
    viewer_id, _ = users[MembershipRole.viewer]

    async with _client() as client:
        promoted = await client.patch(
            f"/api/v1/members/{viewer_id}", json={"role": "agent"}, headers=owner_headers
        )
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "agent"

        removed = await client.delete(f"/api/v1/members/{viewer_id}", headers=owner_headers)
        assert removed.status_code == 204

        members = await client.get("/api/v1/members", headers=owner_headers)
        assert members.status_code == 200
        assert str(viewer_id) not in [m["user_id"] for m in members.json()]
