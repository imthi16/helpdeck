import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

# Superuser connection: migrations, seed, and tests (bypasses RLS).
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Restricted role the app serves requests as; RLS is enforced against it.
app_engine = create_async_engine(_settings.app_database_url, pool_pre_ping=True)
app_session_factory = async_sessionmaker(app_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_session(
    org_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """An app-role session scoped to one tenant via ``app.current_tenant``.

    The context manager owns a single transaction (``session.begin()``): the
    tenant setting is applied transaction-locally and stays in effect for every
    query in the block. On clean exit the transaction commits; on error it rolls
    back. Callers must NOT commit inside the block — committing would end the
    transaction and drop ``app.current_tenant``, leaving later queries unscoped
    (they would then fail closed under RLS). Do all the work, then let the block
    commit for you.

    ``session_factory`` overrides the module-global app factory (used by tests
    that need a loop-local engine); production callers omit it.
    """
    factory = session_factory or app_session_factory
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(org_id)},
        )
        yield session
