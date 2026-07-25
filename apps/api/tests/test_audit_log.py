"""The 5.4 Verify: sensitive actions produce rows, and the app cannot rewrite them.

Append-only is a privilege, not a promise: the app role holds SELECT + INSERT on
`audit_logs` and nothing else, so UPDATE/DELETE fail at the database.
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.main import app
from app.models import AuditLog, Membership, MembershipRole, Organization, User
from app.routers.api_keys import get_api_keys_sessionmaker
from app.routers.audit import get_audit_sessionmaker
from app.routers.auth import get_auth_sessionmaker
from app.routers.deps import get_app_session_factory
from app.routers.documents import get_documents_storage, get_ingest_queue
from app.routers.members import get_members_sessionmaker
from app.routers.onboarding import get_onboarding_sessionmaker
from app.services.storage import LocalFileStorage

Sessionmaker = async_sessionmaker[AsyncSession]
PASSWORD = "hunter2pw"


class StubQueue:
    async def enqueue_ingest(self, document_id: uuid.UUID) -> None:
        return None


@pytest.fixture(autouse=True)
def overrides(
    db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker, tmp_path
) -> AsyncIterator[None]:
    for seam in (
        get_auth_sessionmaker,
        get_members_sessionmaker,
        get_api_keys_sessionmaker,
        get_onboarding_sessionmaker,
    ):
        app.dependency_overrides[seam] = lambda: db_sessionmaker
    # The writer, the viewer and the tenant path all run on the app role.
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    app.dependency_overrides[get_audit_sessionmaker] = lambda: app_sessionmaker
    app.dependency_overrides[get_documents_storage] = lambda: LocalFileStorage(tmp_path)
    app.dependency_overrides[get_ingest_queue] = lambda: StubQueue()
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _signed_up(prefix: str = "audit") -> tuple[httpx.AsyncClient, str, uuid.UUID]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    client = _client()
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "name": "Owner", "org_name": "AuditOrg"},
    )
    assert resp.status_code == 201, resp.text
    return client, email, uuid.UUID(resp.json()["memberships"][0]["org_id"])


async def _cleanup(sm: Sessionmaker, *emails: str) -> None:
    async with sm() as session:
        org_ids: list[uuid.UUID] = []
        for email in emails:
            user = await session.scalar(
                select(User).where(User.email == email).options(selectinload(User.memberships))
            )
            if user is None:
                continue
            org_ids.extend(m.org_id for m in user.memberships)
            await session.delete(user)
        await session.commit()
        for org_id in set(org_ids):
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
        await session.commit()


async def _actions(client: httpx.AsyncClient) -> list[str]:
    response = await client.get("/api/v1/audit")
    assert response.status_code == 200, response.text
    return [event["action"] for event in response.json()]


async def test_signup_and_login_are_recorded(db_sessionmaker: Sessionmaker) -> None:
    owner, email, _ = await _signed_up()
    try:
        assert await _actions(owner) == ["auth.signup"]

        async with _client() as second:
            assert (
                await second.post("/auth/login", json={"email": email, "password": PASSWORD})
            ).status_code == 200

        # Newest first.
        assert await _actions(owner) == ["auth.login", "auth.signup"]
        events = (await owner.get("/api/v1/audit")).json()
        assert events[0]["actor_email"] == email
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_member_key_document_and_settings_actions_are_recorded(
    db_sessionmaker: Sessionmaker,
) -> None:
    owner, email, org_id = await _signed_up()
    invitee = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    try:
        # Member lifecycle.
        invite = await owner.post(
            "/api/v1/members/invites", json={"email": invitee, "role": "viewer"}
        )
        assert invite.status_code == 201
        assert (
            await owner.delete(f"/api/v1/members/invites/{invite.json()['id']}")
        ).status_code == 204

        # Keys.
        key = await owner.post("/api/v1/keys", json={"kind": "secret", "name": "CI"})
        assert key.status_code == 201
        raw_secret = key.json()["secret"]
        assert (await owner.delete(f"/api/v1/keys/{key.json()['id']}")).status_code == 204

        # A document, deleted.
        document = await owner.post(
            "/api/v1/documents", json={"source_type": "text", "title": "Doomed", "content": "x"}
        )
        assert document.status_code == 201
        assert (await owner.delete(f"/api/v1/documents/{document.json()['id']}")).status_code == 204

        # Settings.
        assert (
            await owner.post("/api/v1/onboarding/complete", json={"org_name": "Renamed"})
        ).status_code == 200

        actions = await _actions(owner)
        assert set(actions) == {
            "auth.signup",
            "member.invited",
            "member.invite_revoked",
            "api_key.created",
            "api_key.revoked",
            "document.deleted",
            "settings.updated",
        }
        # Each action recorded exactly once.
        assert len(actions) == 7

        events = {e["action"]: e for e in (await owner.get("/api/v1/audit")).json()}
        assert events["member.invited"]["metadata"] == {"email": invitee, "role": "viewer"}
        assert events["document.deleted"]["metadata"] == {"title": "Doomed"}
        assert events["api_key.created"]["metadata"] == {"kind": "secret", "name": "CI"}
        # Neither the key nor any prefix of it reaches the permanent trail.
        trail = str((await owner.get("/api/v1/audit")).json())
        assert raw_secret not in trail
        assert raw_secret[:11] not in trail
        assert all(e["actor_email"] == email for e in events.values())
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email, invitee)


async def test_removed_member_still_named_after_user_is_deleted(
    db_sessionmaker: Sessionmaker,
) -> None:
    owner, owner_email, org_id = await _signed_up()
    agent_email = f"agent-{uuid.uuid4().hex[:8]}@example.com"
    try:
        async with db_sessionmaker() as session:
            user = User(email=agent_email, password_hash=hash_password(PASSWORD), name="A")
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_id, user_id=user.id, role=MembershipRole.agent))
            await session.commit()
            user_id = user.id

        assert (
            await owner.patch(f"/api/v1/members/{user_id}", json={"role": "admin"})
        ).status_code == 200
        assert (await owner.delete(f"/api/v1/members/{user_id}")).status_code == 204

        # Delete the user account entirely; the trail must still name them.
        async with db_sessionmaker() as session:
            row = await session.get(User, user_id)
            await session.delete(row)
            await session.commit()

        events = {e["action"]: e for e in (await owner.get("/api/v1/audit")).json()}
        assert events["member.removed"]["metadata"] == {"email": agent_email}
        assert events["member.role_changed"]["metadata"]["role"] == "admin"
        assert events["member.removed"]["actor_email"] == owner_email
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, agent_email)


async def test_app_role_cannot_update_or_delete_audit_rows(
    app_sessionmaker: Sessionmaker, db_sessionmaker: Sessionmaker
) -> None:
    """The append-only proof: the grant is SELECT + INSERT, nothing more."""
    async with db_sessionmaker() as session:
        org = Organization(name=f"audit-{uuid.uuid4().hex[:6]}")
        session.add(org)
        await session.flush()
        session.add(AuditLog(org_id=org.id, action="auth.login", actor_email="a@example.com"))
        await session.commit()
        org_id = org.id

    try:
        async with app_sessionmaker() as session:
            # It can read...
            count = await session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE org_id = :org"), {"org": str(org_id)}
            )
            assert count == 1

            # ...and append...
            await session.execute(
                text(
                    "INSERT INTO audit_logs (id, org_id, action, created_at) "
                    "VALUES (gen_random_uuid(), :org, 'auth.login', now())"
                ),
                {"org": str(org_id)},
            )
            await session.commit()

        # ...but never rewrite or erase.
        async with app_sessionmaker() as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(text("UPDATE audit_logs SET action = 'tampered'"))
            await session.rollback()

        async with app_sessionmaker() as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(text("DELETE FROM audit_logs"))
            await session.rollback()
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def test_audit_is_org_scoped_and_admin_only(db_sessionmaker: Sessionmaker) -> None:
    owner_a, email_a, org_a = await _signed_up("a")
    owner_b, email_b, _ = await _signed_up("b")
    viewer_email = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
    try:
        # B's log never contains A's events.
        a_events = (await owner_a.get("/api/v1/audit")).json()
        b_events = (await owner_b.get("/api/v1/audit")).json()
        assert {e["actor_email"] for e in a_events} == {email_a}
        assert {e["actor_email"] for e in b_events} == {email_b}

        # Viewers and agents cannot read the log at all.
        async with db_sessionmaker() as session:
            user = User(email=viewer_email, password_hash=hash_password(PASSWORD), name="V")
            session.add(user)
            await session.flush()
            session.add(Membership(org_id=org_a, user_id=user.id, role=MembershipRole.viewer))
            await session.commit()
        async with _client() as viewer:
            assert (
                await viewer.post("/auth/login", json={"email": viewer_email, "password": PASSWORD})
            ).status_code == 200
            assert (await viewer.get("/api/v1/audit")).status_code == 403
    finally:
        await owner_a.aclose()
        await owner_b.aclose()
        await _cleanup(db_sessionmaker, email_a, email_b, viewer_email)


async def test_audit_requires_authentication() -> None:
    async with _client() as anon:
        assert (await anon.get("/api/v1/audit")).status_code == 401
