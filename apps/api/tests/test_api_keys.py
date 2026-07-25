"""The 5.3 Verify: revocation takes effect immediately and usage is recorded.

Also covers the reveal-once contract for secret keys and who may manage keys.
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.main import app
from app.models import ApiKey, Membership, MembershipRole, Organization, User
from app.routers.api_keys import get_api_keys_sessionmaker
from app.routers.auth import get_auth_sessionmaker
from app.routers.deps import get_app_session_factory
from app.routers.widget import get_widget_rate_limiter, get_widget_sessionmaker

Sessionmaker = async_sessionmaker[AsyncSession]
PASSWORD = "hunter2pw"


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker) -> AsyncIterator[None]:
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_api_keys_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_widget_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    # Rate limiting is exercised in test_widget_api; keep it out of the way here.
    app.dependency_overrides[get_widget_rate_limiter] = lambda: None
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _signed_up(prefix: str = "keys") -> tuple[httpx.AsyncClient, str, uuid.UUID]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    client = _client()
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "name": "Owner", "org_name": "KeyOrg"},
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


async def _add_member(
    sm: Sessionmaker, org_id: uuid.UUID, role: MembershipRole, email: str
) -> httpx.AsyncClient:
    async with sm() as session:
        user = User(email=email, password_hash=hash_password(PASSWORD), name=str(role))
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org_id, user_id=user.id, role=role))
        await session.commit()
    client = _client()
    assert (
        await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    ).status_code == 200
    return client


async def test_signup_issues_a_real_widget_key(db_sessionmaker: Sessionmaker) -> None:
    owner, email, org_id = await _signed_up()
    try:
        widget = await owner.get("/api/v1/keys/widget")
        assert widget.status_code == 200
        body = widget.json()
        assert body["kind"] == "public"
        assert body["value"].startswith("pk_")
        assert body["scopes"] == ["widget:chat"]
        assert body["last_used_at"] is None

        # It authenticates the widget for that org.
        async with _client() as anon:
            config = await anon.get(
                "/api/v1/widget/config", headers={"X-Public-Key": body["value"]}
            )
        assert config.status_code == 200
        assert config.json()["org_name"] == "KeyOrg"
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_revoked_key_401s_on_the_next_request(db_sessionmaker: Sessionmaker) -> None:
    """The 5.3 Verify: revocation is immediate, with no cache in front of it."""
    owner, email, _ = await _signed_up()
    try:
        widget = (await owner.get("/api/v1/keys/widget")).json()
        raw = widget["value"]

        async with _client() as anon:
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": raw})
            ).status_code == 200

            revoked = await owner.delete(f"/api/v1/keys/{widget['id']}")
            assert revoked.status_code == 204

            # Same key, very next call.
            denied = await anon.get("/api/v1/widget/config", headers={"X-Public-Key": raw})
            assert denied.status_code == 401
            assert denied.json()["detail"] == "invalid public key"

        # It is still listed, marked revoked, rather than vanishing.
        listed = (await owner.get("/api/v1/keys")).json()
        assert [k["revoked_at"] is not None for k in listed] == [True]
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_last_used_at_records_usage(db_sessionmaker: Sessionmaker) -> None:
    """The 5.3 Verify: last_used_at is NULL until the key is actually used."""
    owner, email, _ = await _signed_up()
    try:
        widget = (await owner.get("/api/v1/keys/widget")).json()
        assert widget["last_used_at"] is None

        async with _client() as anon:
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": widget["value"]})
            ).status_code == 200

        after = (await owner.get("/api/v1/keys/widget")).json()
        assert after["last_used_at"] is not None

        # Repeat use inside the throttle window does not rewrite the timestamp.
        async with _client() as anon:
            await anon.get("/api/v1/widget/config", headers={"X-Public-Key": widget["value"]})
        again = (await owner.get("/api/v1/keys/widget")).json()
        assert again["last_used_at"] == after["last_used_at"]
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_secret_key_is_revealed_once_and_never_stored_in_clear(
    db_sessionmaker: Sessionmaker,
) -> None:
    owner, email, org_id = await _signed_up()
    try:
        created = await owner.post("/api/v1/keys", json={"kind": "secret", "name": "CI"})
        assert created.status_code == 201, created.text
        body = created.json()
        secret = body["secret"]
        assert secret.startswith("sk_")
        assert body["value"] is None  # not retrievable later
        assert body["prefix"] == secret[:11]

        # Listing never returns the secret again.
        listed = (await owner.get("/api/v1/keys")).json()
        row = next(k for k in listed if k["id"] == body["id"])
        assert row["secret"] is None
        assert row["value"] is None
        assert "key_hash" not in row

        # Only the hash is on disk.
        async with db_sessionmaker() as session:
            stored = await session.get(ApiKey, uuid.UUID(body["id"]))
            assert stored.value is None
            assert stored.key_hash != secret
            assert len(stored.key_hash) == 64

        # A secret key is inert for now: it does not authenticate the widget.
        async with _client() as anon:
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": secret})
            ).status_code == 401
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_another_orgs_key_does_not_grant_access(db_sessionmaker: Sessionmaker) -> None:
    owner_a, email_a, _ = await _signed_up("a")
    owner_b, email_b, _ = await _signed_up("b")
    try:
        key_a = (await owner_a.get("/api/v1/keys/widget")).json()
        key_b = (await owner_b.get("/api/v1/keys/widget")).json()

        # Org B cannot revoke org A's key.
        assert (await owner_b.delete(f"/api/v1/keys/{key_a['id']}")).status_code == 404
        # ...and A's key still works.
        async with _client() as anon:
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": key_a["value"]})
            ).status_code == 200

        # Each org only sees its own keys.
        assert {k["id"] for k in (await owner_a.get("/api/v1/keys")).json()} == {key_a["id"]}
        assert {k["id"] for k in (await owner_b.get("/api/v1/keys")).json()} == {key_b["id"]}
    finally:
        await owner_a.aclose()
        await owner_b.aclose()
        await _cleanup(db_sessionmaker, email_a, email_b)


async def test_revoking_the_widget_key_reissues_one(db_sessionmaker: Sessionmaker) -> None:
    owner, email, _ = await _signed_up()
    try:
        first = (await owner.get("/api/v1/keys/widget")).json()
        assert (await owner.delete(f"/api/v1/keys/{first['id']}")).status_code == 204

        second = (await owner.get("/api/v1/keys/widget")).json()
        assert second["id"] != first["id"]
        assert second["value"] != first["value"]

        async with _client() as anon:
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": second["value"]})
            ).status_code == 200
            assert (
                await anon.get("/api/v1/widget/config", headers={"X-Public-Key": first["value"]})
            ).status_code == 401
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, email)


@pytest.mark.parametrize("role", [MembershipRole.agent, MembershipRole.viewer], ids=str)
async def test_agent_and_viewer_cannot_see_or_manage_keys(
    role: MembershipRole, db_sessionmaker: Sessionmaker
) -> None:
    owner, owner_email, org_id = await _signed_up()
    member_email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    try:
        member = await _add_member(db_sessionmaker, org_id, role, member_email)
        try:
            assert (await member.get("/api/v1/keys")).status_code == 403
            assert (await member.get("/api/v1/keys/widget")).status_code == 403
            assert (await member.post("/api/v1/keys", json={"kind": "public"})).status_code == 403
            key_id = (await owner.get("/api/v1/keys/widget")).json()["id"]
            assert (await member.delete(f"/api/v1/keys/{key_id}")).status_code == 403
        finally:
            await member.aclose()
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, member_email)


async def test_admin_manages_public_keys_but_not_secrets(db_sessionmaker: Sessionmaker) -> None:
    owner, owner_email, org_id = await _signed_up()
    admin_email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    try:
        admin = await _add_member(db_sessionmaker, org_id, MembershipRole.admin, admin_email)
        try:
            # Public keys: yes.
            created = await admin.post("/api/v1/keys", json={"kind": "public", "name": "Site"})
            assert created.status_code == 201
            assert (await admin.delete(f"/api/v1/keys/{created.json()['id']}")).status_code == 204

            # Secrets: owner only.
            assert (await admin.post("/api/v1/keys", json={"kind": "secret"})).status_code == 403
            owner_secret = await owner.post("/api/v1/keys", json={"kind": "secret"})
            assert owner_secret.status_code == 201
            assert (
                await admin.delete(f"/api/v1/keys/{owner_secret.json()['id']}")
            ).status_code == 403
            assert (
                await owner.delete(f"/api/v1/keys/{owner_secret.json()['id']}")
            ).status_code == 204
        finally:
            await admin.aclose()
    finally:
        await owner.aclose()
        await _cleanup(db_sessionmaker, owner_email, admin_email)


async def test_keys_require_authentication() -> None:
    async with _client() as anon:
        assert (await anon.get("/api/v1/keys")).status_code == 401
        assert (await anon.post("/api/v1/keys", json={"kind": "secret"})).status_code == 401
