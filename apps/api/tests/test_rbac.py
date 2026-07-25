"""The 5.2 Verify: every role against every sensitive endpoint.

One org holds all four roles. Each case asserts the *authorization* outcome only:
allowed calls must not come back 403 (a 404/409 from the fixture data is fine and
still proves the role passed the gate), denied calls must be exactly 403.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.main import app
from app.models import (
    Conversation,
    ConversationChannel,
    Document,
    DocumentSourceType,
    DocumentStatus,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from app.routers.auth import get_auth_sessionmaker
from app.routers.deps import get_app_session_factory
from app.routers.documents import get_documents_storage, get_ingest_queue
from app.routers.members import get_members_sessionmaker
from app.routers.onboarding import get_onboarding_sessionmaker
from app.services.storage import LocalFileStorage

Sessionmaker = async_sessionmaker[AsyncSession]

ALL_ROLES = [
    MembershipRole.owner,
    MembershipRole.admin,
    MembershipRole.agent,
    MembershipRole.viewer,
]
PASSWORD = "hunter2pw"


class StubQueue:
    async def enqueue_ingest(self, document_id: uuid.UUID) -> None:
        return None


@dataclass
class Org:
    org_id: uuid.UUID
    document_id: uuid.UUID
    conversation_id: uuid.UUID
    emails: dict[MembershipRole, str]


@pytest.fixture(autouse=True)
def overrides(
    db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker, tmp_path
) -> AsyncIterator[None]:
    # Identity-table handlers get the loop-local superuser sessionmaker; tenant
    # tables go through the restricted role, as in production.
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_members_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_onboarding_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    app.dependency_overrides[get_documents_storage] = lambda: LocalFileStorage(tmp_path)
    app.dependency_overrides[get_ingest_queue] = lambda: StubQueue()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def org(db_sessionmaker: Sessionmaker) -> AsyncIterator[Org]:
    """One org with a user per role, plus a document and a conversation."""
    suffix = uuid.uuid4().hex[:8]
    emails = {role: f"rbac-{role}-{suffix}@example.com" for role in ALL_ROLES}
    async with db_sessionmaker() as session:
        organization = Organization(name=f"rbac-{suffix}", onboarded=True)
        session.add(organization)
        await session.flush()
        for role, email in emails.items():
            user = User(email=email, password_hash=hash_password(PASSWORD), name=str(role))
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=organization.id, user_id=user.id, role=role))
        document = Document(
            org_id=organization.id,
            title="rbac doc",
            source_type=DocumentSourceType.text,
            status=DocumentStatus.ready,
        )
        conversation = Conversation(org_id=organization.id, channel=ConversationChannel.playground)
        session.add_all([document, conversation])
        await session.commit()
        created = Org(
            org_id=organization.id,
            document_id=document.id,
            conversation_id=conversation.id,
            emails=emails,
        )

    try:
        yield created
    finally:
        async with db_sessionmaker() as session:
            org_row = await session.get(Organization, created.org_id)
            if org_row is not None:
                await session.delete(org_row)
            for email in emails.values():
                user = await session.scalar(
                    select(User).where(User.email == email).options(selectinload(User.memberships))
                )
                if user is not None:
                    await session.delete(user)
            await session.commit()


async def _client_for(role: MembershipRole, org: Org) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    resp = await client.post("/auth/login", json={"email": org.emails[role], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return client


# (label, call) — each call takes the client and the seeded org.
Call = Callable[[httpx.AsyncClient, Org], object]

READ_CASES: dict[str, Call] = {
    "list_documents": lambda c, o: c.get("/api/v1/documents"),
    "get_document": lambda c, o: c.get(f"/api/v1/documents/{o.document_id}"),
    "list_conversations": lambda c, o: c.get("/api/v1/conversations"),
    "get_conversation": lambda c, o: c.get(f"/api/v1/conversations/{o.conversation_id}"),
    "list_members": lambda c, o: c.get("/api/v1/members"),
    "list_invites": lambda c, o: c.get("/api/v1/members/invites"),
}

# Writes, with the roles allowed to perform them.
WRITE_CASES: dict[str, tuple[Call, set[MembershipRole]]] = {
    "create_document": (
        lambda c, o: c.post(
            "/api/v1/documents", json={"source_type": "text", "title": "t", "content": "body"}
        ),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "reindex_document": (
        lambda c, o: c.post(f"/api/v1/documents/{o.document_id}/reindex"),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "delete_document": (
        lambda c, o: c.delete(f"/api/v1/documents/{o.document_id}"),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "resolve_conversation": (
        lambda c, o: c.post(f"/api/v1/conversations/{o.conversation_id}/resolve"),
        {MembershipRole.owner, MembershipRole.admin, MembershipRole.agent},
    ),
    "reply_to_conversation": (
        lambda c, o: c.post(
            f"/api/v1/conversations/{o.conversation_id}/reply", json={"content": "hi"}
        ),
        {MembershipRole.owner, MembershipRole.admin, MembershipRole.agent},
    ),
    "complete_onboarding": (
        lambda c, o: c.post("/api/v1/onboarding/complete", json={}),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "playground_chat": (
        lambda c, o: c.post(
            "/api/v1/chat", json={"org_id": str(o.org_id), "message": "hello there"}
        ),
        {MembershipRole.owner, MembershipRole.admin, MembershipRole.agent},
    ),
    "invite_member": (
        lambda c, o: c.post(
            "/api/v1/members/invites",
            json={"email": f"invitee-{uuid.uuid4().hex[:8]}@example.com", "role": "viewer"},
        ),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "revoke_invite": (
        lambda c, o: c.delete(f"/api/v1/members/invites/{uuid.uuid4()}"),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "change_role": (
        lambda c, o: c.patch(f"/api/v1/members/{uuid.uuid4()}", json={"role": "agent"}),
        {MembershipRole.owner, MembershipRole.admin},
    ),
    "remove_member": (
        lambda c, o: c.delete(f"/api/v1/members/{uuid.uuid4()}"),
        {MembershipRole.owner, MembershipRole.admin},
    ),
}


@pytest.mark.parametrize("role", ALL_ROLES, ids=str)
@pytest.mark.parametrize("case", sorted(READ_CASES), ids=str)
async def test_reads_allowed_for_every_role(role: MembershipRole, case: str, org: Org) -> None:
    client = await _client_for(role, org)
    try:
        response = await READ_CASES[case](client, org)
        assert response.status_code == 200, f"{role} {case} -> {response.status_code}"
    finally:
        await client.aclose()


@pytest.mark.parametrize("role", ALL_ROLES, ids=str)
@pytest.mark.parametrize("case", sorted(WRITE_CASES), ids=str)
async def test_write_matrix(role: MembershipRole, case: str, org: Org) -> None:
    call, allowed = WRITE_CASES[case]
    client = await _client_for(role, org)
    try:
        response = await call(client, org)
        if role in allowed:
            # The gate passed; 404/409 from the placeholder ids is not our concern.
            assert response.status_code != 403, f"{role} should be allowed {case}"
        else:
            assert response.status_code == 403, (
                f"{role} should be denied {case}, got {response.status_code}"
            )
    finally:
        await client.aclose()


async def test_chat_requires_authentication() -> None:
    """The playground endpoint used to accept any org_id with no session at all."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat", json={"org_id": str(uuid.uuid4()), "message": "hello"}
        )
    assert response.status_code == 401


async def test_chat_rejects_another_orgs_id(org: Org, db_sessionmaker: Sessionmaker) -> None:
    """A member may only chat against their own org, whatever the body says."""
    async with db_sessionmaker() as session:
        other = Organization(name=f"other-{uuid.uuid4().hex[:6]}", onboarded=True)
        session.add(other)
        await session.commit()
        other_id = other.id

    client = await _client_for(MembershipRole.owner, org)
    try:
        response = await client.post(
            "/api/v1/chat", json={"org_id": str(other_id), "message": "hello"}
        )
        assert response.status_code == 403
    finally:
        await client.aclose()
        async with db_sessionmaker() as session:
            row = await session.get(Organization, other_id)
            if row is not None:
                await session.delete(row)
                await session.commit()
