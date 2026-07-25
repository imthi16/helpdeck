"""Members management: invite -> accept, role changes, removal, and the guards.

The service-layer invariants are the interesting part: an org always keeps an
owner, only an owner moves ownership, and an invite link never bypasses an
existing account's password.
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.main import app
from app.models import Invitation, Membership, MembershipRole, Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.members import get_members_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]
PASSWORD = "hunter2pw"


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker) -> AsyncIterator[None]:
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_members_sessionmaker] = lambda: db_sessionmaker
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _signed_up(prefix: str) -> tuple[httpx.AsyncClient, str, uuid.UUID]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    client = _client()
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "name": "Owner", "org_name": "MemberOrg"},
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


def _token_from(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


async def test_invite_then_accept_creates_member(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up("owner")
    invitee_email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    try:
        created = await owner.post(
            "/api/v1/members/invites", json={"email": invitee_email, "role": "viewer"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["invite_url"].endswith(_token_from(body["invite_url"]))
        assert body["role"] == "viewer"

        # Pending invite is listed (without the token).
        listed = await owner.get("/api/v1/members/invites")
        assert [i["email"] for i in listed.json()] == [invitee_email]
        assert listed.json()[0].get("invite_url") is None

        token = _token_from(body["invite_url"])

        # The accept page can describe the invite before redeeming it.
        async with _client() as anon:
            preview = await anon.get(f"/auth/invites/{token}")
            assert preview.status_code == 200
            assert preview.json()["email"] == invitee_email
            assert preview.json()["needs_account"] is True

            # A brand-new person redeems it, which creates their account.
            accepted = await anon.post(
                f"/auth/invites/{token}/accept",
                json={"name": "Vee", "password": PASSWORD},
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["email"] == invitee_email
            assert accepted.json()["memberships"][0]["role"] == "viewer"
            # Redeeming signed them in.
            me = await anon.get("/auth/me")
            assert me.status_code == 200

        # The roster now shows both people, and the invite is no longer pending.
        members = await owner.get("/api/v1/members")
        assert sorted(m["email"] for m in members.json()) == sorted([owner_email, invitee_email])
        assert (await owner.get("/api/v1/members/invites")).json() == []

        # The token is single-use.
        async with _client() as anon:
            replay = await anon.post(
                f"/auth/invites/{token}/accept", json={"name": "x", "password": PASSWORD}
            )
            assert replay.status_code == 400
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, invitee_email)


async def test_existing_account_must_sign_in_to_accept(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up("owner")
    other, other_email, _ = await _signed_up("other")
    try:
        created = await owner.post(
            "/api/v1/members/invites", json={"email": other_email, "role": "agent"}
        )
        token = _token_from(created.json()["invite_url"])

        # Holding the link is not enough for an account that already exists.
        async with _client() as anon:
            assert (await anon.get(f"/auth/invites/{token}")).json()["needs_account"] is False
            blocked = await anon.post(
                f"/auth/invites/{token}/accept", json={"name": "", "password": PASSWORD}
            )
            assert blocked.status_code == 403

        # Signed in as the invited user, it works.
        accepted = await other.post(f"/auth/invites/{token}/accept", json={"name": ""})
        assert accepted.status_code == 200, accepted.text
        roles = {m["org_id"]: m["role"] for m in accepted.json()["memberships"]}
        assert roles[str(org_id)] == "agent"
    finally:
        await owner.aclose()
        await other.aclose()
        await _cleanup(db_sessionmaker, owner_email, other_email)


async def test_wrong_signed_in_user_cannot_accept(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, _ = await _signed_up("owner")
    bystander, bystander_email, _ = await _signed_up("bystander")
    try:
        created = await owner.post(
            "/api/v1/members/invites",
            json={"email": f"someone-{uuid.uuid4().hex[:6]}@example.com", "role": "viewer"},
        )
        token = _token_from(created.json()["invite_url"])
        response = await bystander.post(f"/auth/invites/{token}/accept", json={"name": ""})
        assert response.status_code == 403
    finally:
        await owner.aclose()
        await bystander.aclose()
        await _cleanup(db_sessionmaker, owner_email, bystander_email)


async def test_expired_and_unknown_tokens_rejected(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, _ = await _signed_up("owner")
    invitee_email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    try:
        created = await owner.post(
            "/api/v1/members/invites", json={"email": invitee_email, "role": "viewer"}
        )
        token = _token_from(created.json()["invite_url"])

        async with _client() as anon:
            assert (await anon.get(f"/auth/invites/{uuid.uuid4().hex}")).status_code == 400

            # Backdate the expiry, then the same token is refused.
            async with db_sessionmaker() as session:
                invitation = await session.get(Invitation, uuid.UUID(created.json()["id"]))
                invitation.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
                await session.commit()

            assert (await anon.get(f"/auth/invites/{token}")).status_code == 400
            expired = await anon.post(
                f"/auth/invites/{token}/accept", json={"name": "", "password": PASSWORD}
            )
            assert expired.status_code == 400
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, invitee_email)


async def test_reinviting_replaces_the_pending_invite(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, _ = await _signed_up("owner")
    invitee_email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    try:
        first = await owner.post(
            "/api/v1/members/invites", json={"email": invitee_email, "role": "viewer"}
        )
        second = await owner.post(
            "/api/v1/members/invites", json={"email": invitee_email, "role": "admin"}
        )
        assert second.status_code == 201
        assert second.json()["invite_url"] != first.json()["invite_url"]

        # One pending invite, with the new role; the old token is dead.
        pending = (await owner.get("/api/v1/members/invites")).json()
        assert len(pending) == 1
        assert pending[0]["role"] == "admin"
        async with _client() as anon:
            old = await anon.get(f"/auth/invites/{_token_from(first.json()['invite_url'])}")
            assert old.status_code == 400
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, invitee_email)


async def test_revoke_invite(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, _ = await _signed_up("owner")
    invitee_email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    try:
        created = await owner.post(
            "/api/v1/members/invites", json={"email": invitee_email, "role": "viewer"}
        )
        invite_id = created.json()["id"]
        assert (await owner.delete(f"/api/v1/members/invites/{invite_id}")).status_code == 204
        assert (await owner.get("/api/v1/members/invites")).json() == []
        # Revoking twice is a 404, and the token no longer resolves.
        assert (await owner.delete(f"/api/v1/members/invites/{invite_id}")).status_code == 404
        async with _client() as anon:
            token = _token_from(created.json()["invite_url"])
            assert (await anon.get(f"/auth/invites/{token}")).status_code == 400
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, invitee_email)


async def test_inviting_an_existing_member_conflicts(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, _ = await _signed_up("owner")
    try:
        response = await owner.post(
            "/api/v1/members/invites", json={"email": owner_email, "role": "admin"}
        )
        assert response.status_code == 409
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email)


async def _add_member(
    sm: Sessionmaker, org_id: uuid.UUID, role: MembershipRole, email: str
) -> uuid.UUID:
    async with sm() as session:
        user = User(email=email, password_hash=hash_password(PASSWORD), name=str(role))
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org_id, user_id=user.id, role=role))
        await session.commit()
        return user.id


async def test_change_role_and_remove_member(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up("owner")
    agent_email = f"agent-{uuid.uuid4().hex[:8]}@example.com"
    try:
        user_id = await _add_member(db_sessionmaker, org_id, MembershipRole.agent, agent_email)

        promoted = await owner.patch(f"/api/v1/members/{user_id}", json={"role": "admin"})
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "admin"

        removed = await owner.delete(f"/api/v1/members/{user_id}")
        assert removed.status_code == 204
        assert [m["email"] for m in (await owner.get("/api/v1/members")).json()] == [owner_email]

        # Gone: further operations 404.
        assert (
            await owner.patch(f"/api/v1/members/{user_id}", json={"role": "agent"})
        ).status_code == 404
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, agent_email)


async def test_last_owner_cannot_be_demoted_or_removed(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up("owner")
    try:
        me = (await owner.get("/auth/me")).json()
        demote = await owner.patch(f"/api/v1/members/{me['id']}", json={"role": "admin"})
        assert demote.status_code == 409
        remove = await owner.delete(f"/api/v1/members/{me['id']}")
        assert remove.status_code == 409

        # With a second owner, stepping down is allowed.
        second_email = f"owner2-{uuid.uuid4().hex[:8]}@example.com"
        await _add_member(db_sessionmaker, org_id, MembershipRole.owner, second_email)
        try:
            assert (
                await owner.patch(f"/api/v1/members/{me['id']}", json={"role": "admin"})
            ).status_code == 200
        finally:
            await _cleanup(db_sessionmaker, second_email)
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email)


async def test_admin_cannot_touch_ownership(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up("owner")
    admin_email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    try:
        admin_user_id = await _add_member(
            db_sessionmaker, org_id, MembershipRole.admin, admin_email
        )
        admin = _client()
        try:
            assert (
                await admin.post("/auth/login", json={"email": admin_email, "password": PASSWORD})
            ).status_code == 200
            owner_user_id = (await owner.get("/auth/me")).json()["id"]

            # An admin may not grant ownership...
            assert (
                await admin.patch(f"/api/v1/members/{admin_user_id}", json={"role": "owner"})
            ).status_code == 403
            # ...nor take it away.
            assert (
                await admin.patch(f"/api/v1/members/{owner_user_id}", json={"role": "viewer"})
            ).status_code == 403
            assert (await admin.delete(f"/api/v1/members/{owner_user_id}")).status_code == 403
            # ...nor invite one.
            assert (
                await admin.post(
                    "/api/v1/members/invites",
                    json={"email": f"o-{uuid.uuid4().hex[:6]}@example.com", "role": "owner"},
                )
            ).status_code == 403
        finally:
            await admin.aclose()
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, admin_email)
