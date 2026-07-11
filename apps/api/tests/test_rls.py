"""Row-Level Security isolation tests.

Setup runs as the superuser (bypasses RLS); the assertions run through the app
role, which RLS is enforced against. Each test uses a loop-local app engine
(the module-global one in app.core.db is bound to the server's loop at runtime).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.models import (
    Chunk,
    Document,
    DocumentSourceType,
    DocumentStatus,
    Organization,
)

Sessionmaker = async_sessionmaker[AsyncSession]


class AppDb:
    def __init__(self, factory: Sessionmaker) -> None:
        self.factory = factory

    def session(self) -> AsyncSession:
        return self.factory()

    @asynccontextmanager
    async def tenant(self, org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self.factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(org_id)},
            )
            yield session


@pytest.fixture
async def app_db() -> AsyncIterator[AppDb]:
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
            await session.commit()
