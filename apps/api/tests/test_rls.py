"""Row-Level Security isolation tests.

Setup runs as the superuser (bypasses RLS); the assertions run through the app
role, which RLS is enforced against. Each test uses a loop-local app engine
(the module-global one in app.core.db is bound to the server's loop at runtime).
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.db import tenant_session
from app.models import (
    Chunk,
    Document,
    DocumentSourceType,
    DocumentStatus,
    Membership,
    MembershipRole,
    Organization,
    User,
)

Sessionmaker = async_sessionmaker[AsyncSession]


class AppDb:
    def __init__(self, factory: Sessionmaker) -> None:
        self.factory = factory

    def session(self) -> AsyncSession:
        return self.factory()

    def tenant(self, org_id: uuid.UUID):
        # Exercise the production helper (including its transaction ownership),
        # passing the loop-local factory this test's engine is bound to.
        return tenant_session(org_id, session_factory=self.factory)


@pytest.fixture
async def app_db(app_login: None) -> AsyncIterator[AppDb]:
    # app_login provisions the app role's LOGIN/password (the migration no longer
    # does — see conftest) so this engine can actually connect as helpdeck_app.
    engine = create_async_engine(get_settings().app_database_url)
    try:
        yield AppDb(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def _make_org_with_doc(sm: Sessionmaker, title: str) -> uuid.UUID:
    async with sm() as session:
        org = Organization(name=f"rls-{uuid.uuid4()}")
        session.add(org)
        await session.flush()
        document = Document(
            org_id=org.id,
            title=title,
            source_type=DocumentSourceType.text,
            status=DocumentStatus.ready,
        )
        session.add(document)
        await session.flush()
        session.add(
            Chunk(
                org_id=org.id,
                document_id=document.id,
                content=f"content for {title}",
                meta={},
                token_count=3,
            )
        )
        await session.commit()
        return org.id


@pytest.fixture
async def two_orgs(db_sessionmaker: Sessionmaker):
    org_a = await _make_org_with_doc(db_sessionmaker, "Org A doc")
    org_b = await _make_org_with_doc(db_sessionmaker, "Org B doc")
    try:
        yield org_a, org_b
    finally:
        async with db_sessionmaker() as session:
            for org_id in (org_a, org_b):
                org = await session.get(Organization, org_id)
                if org is not None:
                    await session.delete(org)
            await session.commit()


async def test_unscoped_query_only_sees_current_tenant(
    app_db: AppDb, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs

    # A deliberately unscoped "SELECT * FROM documents" still only returns Org A.
    async with app_db.tenant(org_a) as session:
        titles = (await session.execute(text("SELECT title FROM documents"))).scalars().all()
    assert "Org A doc" in titles
    assert "Org B doc" not in titles

    async with app_db.tenant(org_b) as session:
        titles = (await session.execute(text("SELECT title FROM documents"))).scalars().all()
    assert "Org B doc" in titles
    assert "Org A doc" not in titles

    async with app_db.tenant(org_a) as session:
        count = await session.scalar(text("SELECT count(*) FROM chunks"))
    assert count == 1


async def test_no_tenant_context_fails_closed(
    app_db: AppDb, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    # App-role session with no app.current_tenant set -> zero rows, not all rows.
    async with app_db.session() as session:
        count = await session.scalar(text("SELECT count(*) FROM documents"))
    assert count == 0


async def test_app_role_cannot_bypass_rls(
    app_db: AppDb, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    async with app_db.session() as session:
        is_super = await session.scalar(text("SHOW is_superuser"))
        assert is_super == "off"

        # It cannot escalate to the owner/superuser role.
        with pytest.raises((ProgrammingError, DBAPIError)):
            await session.execute(text("SET ROLE helpdeck"))
        await session.rollback()


async def test_with_check_blocks_cross_tenant_write(
    app_db: AppDb, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    # Scoped to A, try to insert a row belonging to B -> WITH CHECK violation.
    # tenant_session owns the transaction, so we never commit here.
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with app_db.tenant(org_a) as session:
            await session.execute(
                text(
                    "INSERT INTO documents (id, org_id, title, source_type, status,"
                    " created_at, updated_at) VALUES (gen_random_uuid(), :org, 't',"
                    " 'text', 'ready', now(), now())"
                ),
                {"org": str(org_b)},
            )


async def test_identity_lane_works_as_app_role(
    app_db: AppDb, db_sessionmaker: Sessionmaker
) -> None:
    """The app role can serve signup/login (identity tables have grants, no RLS)."""
    from app.services.auth import load_user_response, signup

    email = f"rls-identity-{uuid.uuid4().hex[:10]}@example.com"
    async with app_db.session() as session:
        user = await signup(
            session, email=email, password="hunter2pw", name="I", org_name="IdentityOrg"
        )
        await session.commit()  # signup flushes; callers own the commit (5.4)
        loaded = await load_user_response(session, user.id)
    assert loaded is not None
    assert loaded.memberships and loaded.memberships[0].role.value == "owner"

    # Cleanup as superuser.
    async with db_sessionmaker() as session:
        db_user = await session.get(User, user.id)
        org = await session.get(Organization, loaded.memberships[0].org_id)
        if db_user is not None:
            await session.delete(db_user)
        if org is not None:
            await session.delete(org)
        await session.commit()


async def test_documents_endpoint_enforced_by_rls(
    app_db: AppDb,
    two_orgs: tuple[uuid.UUID, uuid.UUID],
    db_sessionmaker: Sessionmaker,
) -> None:
    """End to end: the documents router, run over the app role, only sees the
    authenticated user's org even though another org's rows exist."""
    import httpx

    from app.main import app
    from app.routers.auth import get_auth_sessionmaker
    from app.routers.documents import get_documents_sessionmaker

    org_a, org_b = two_orgs
    email = f"rls-router-{uuid.uuid4().hex[:10]}@example.com"
    async with db_sessionmaker() as session:
        user = User(email=email, password_hash="x", name="R")
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org_a, user_id=user.id, role=MembershipRole.owner))
        await session.commit()
        user_id = user.id

    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_sessionmaker] = lambda: app_db.factory
    try:
        from app.core.security import create_access_token

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        ) as client:
            resp = await client.get("/api/v1/documents")
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()]
        assert titles == ["Org A doc"]
    finally:
        app.dependency_overrides.clear()
        async with db_sessionmaker() as session:
            db_user = await session.get(User, user_id)
            if db_user is not None:
                await session.delete(db_user)
            await session.commit()


async def test_child_cannot_reference_cross_tenant_parent(
    app_db: AppDb,
    two_orgs: tuple[uuid.UUID, uuid.UUID],
    db_sessionmaker: Sessionmaker,
) -> None:
    org_a, org_b = two_orgs
    # Org B's document id, read as superuser (FK checks bypass RLS anyway).
    async with db_sessionmaker() as session:
        doc_b = await session.scalar(
            text("SELECT id FROM documents WHERE org_id = :b"), {"b": str(org_b)}
        )

    # Scoped to A, a chunk with org_id=A that points at B's document passes
    # WITH CHECK (own org_id is A) but the composite (document_id, org_id) FK
    # rejects it: no documents row with (id=doc_b, org_id=A) exists.
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with app_db.tenant(org_a) as session:
            await session.execute(
                text(
                    "INSERT INTO chunks (id, org_id, document_id, content, metadata,"
                    " token_count, created_at, updated_at) VALUES (gen_random_uuid(),"
                    " :a, :doc_b, 'x', '{}', 1, now(), now())"
                ),
                {"a": str(org_a), "doc_b": str(doc_b)},
            )
